import importlib.util
import inspect
import json
import logging
import re
import shutil
import subprocess
import sys
import threading
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from server.config import ServerConfig, SERVER_DIR, get_plugins_dir
from server.web.auth import require_admin

_log = logging.getLogger("uroboros")

BUNDLED_PLUGINS_DIR = Path(__file__).parent / "bundled_plugins"
MANIFEST_FILE_NAME = "manifest.json"
PLUGIN_ENTRY_FILE = "plugin.py"
_PLUGIN_DATA_ROOT = SERVER_DIR / "plugin-data"
_PLUGIN_STATE_FILE = SERVER_DIR / "plugins_state.json"


@dataclass
class TabDef:
    id: str
    title: str
    group: str = "GENERAL"
    order: int = 100
    fragment: str = ""
    fragment_dir: str = ""
    scripts: List[str] = field(default_factory=list)
    css: List[str] = field(default_factory=list)
    loader: str = ""
    in_nav: bool = True
    icon: str = ""


class PluginContext:
    def __init__(self, plugin: "Plugin", plugin_dir: Path, registry: "PluginRegistry"):
        self.plugin = plugin
        self.plugin_id = plugin.id
        self.plugin_dir = Path(plugin_dir)
        self.registry = registry
        self.app: Any = None

    def log(self, level: str, msg: str):
        getattr(_log, level)("[plugin:%s] %s", self.plugin_id, msg)

    def data_dir(self) -> Path:
        path = _PLUGIN_DATA_ROOT / self.plugin_id
        path.mkdir(parents=True, exist_ok=True)
        return path

    def config_file(self) -> Path:
        return self.data_dir() / "config.json"

    def config(self, default: Optional[dict] = None) -> dict:
        try:
            raw = json.loads(self.config_file().read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                return raw
        except Exception:
            pass
        return dict(default or {})

    def save_config(self, data: dict):
        self.config_file().write_text(
            json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8"
        )

    def get_session(self):
        from server.database import get_session

        return get_session()

    def static_url(self, rel: str) -> str:
        return f"/admin/static/plugins/{self.plugin_id}/{rel.lstrip('/')}"


class Plugin:
    id: str = ""
    name: str = ""
    version: str = "1.0.0"
    description: str = ""
    author: str = ""
    homepage: str = ""
    load_priority: int = 100

    def __init__(self):
        self.ctx: Optional[PluginContext] = None
        self._tabs: List[TabDef] = []
        self._routers: List[Tuple[APIRouter, str]] = []
        self._models: List[type] = []
        self._plugin_dir: Optional[Path] = None

    def register_tab(self, tab: TabDef) -> TabDef:
        if not re.match(r"^[a-zA-Z0-9_-]+$", tab.id):
            raise ValueError(f"Invalid tab id {tab.id!r}")
        if self._plugin_dir is not None:
            fragments_dir = self._plugin_dir / "fragments"
            if fragments_dir.is_dir() and not tab.fragment_dir:
                tab.fragment_dir = str(fragments_dir)
        self._tabs.append(tab)
        return tab

    def register_router(self, router: APIRouter, prefix: str = ""):
        self._routers.append((router, prefix))

    def register_model(self, model: type):
        self._models.append(model)
        return model

    def on_load(self, ctx: PluginContext):
        pass

    async def on_startup(self, ctx: PluginContext):
        pass

    async def on_shutdown(self, ctx: PluginContext):
        pass

    def on_install(self, ctx: PluginContext):
        pass

    def on_uninstall(self, ctx: PluginContext):
        pass


class PluginEntry:
    def __init__(self, plugin_id: str, plugin_dir: Path, manifest: dict):
        self.id = plugin_id
        self.dir = plugin_dir
        self.manifest = manifest
        self.enabled = True
        self.loaded = False
        self.error = ""
        self.instance: Optional[Plugin] = None
        self.context: Optional[PluginContext] = None


class EventBus:
    def __init__(self):
        self._handlers = defaultdict(list)
        self._lock = threading.RLock()

    def on(self, name: str, fn: Optional[Callable] = None):
        if fn is None:
            def _deco(func):
                self.on(name, func)
                return func
            return _deco

        with self._lock:
            self._handlers[name].append(fn)
        return fn

    def off(self, name: str, fn: Callable):
        with self._lock:
            try:
                self._handlers[name].remove(fn)
            except ValueError:
                pass

    def emit(self, name: str, **data):
        with self._lock:
            handlers = list(self._handlers.get(name, ()))
        if not handlers:
            return

        def _run():
            for fn in handlers:
                try:
                    fn(**data)
                except Exception:
                    _log.exception("[event:%s] handler failed", name)

        threading.Thread(target=_run, daemon=True, name=f"event-{name}").start()


def _read_manifest(path: Path) -> dict:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(raw, dict):
            return raw
    except Exception:
        pass
    return {}


def _dependency_items(manifest: dict) -> List[tuple]:
    items = []
    for req in manifest.get("requirements") or []:
        if isinstance(req, dict):
            package = str(req.get("package", "")).strip()
            import_name = str(req.get("import", "")).strip()
            if not import_name:
                import_name = package.split(">=", 1)[0].split("==", 1)[0].split(";", 1)[0].split("~=", 1)[0].strip()
        elif isinstance(req, str):
            package = req.strip()
            import_name = package.split(">=", 1)[0].split("==", 1)[0].split(";", 1)[0].split("~=", 1)[0].strip()
        else:
            continue
        if package and import_name:
            items.append((package, import_name))
    return items


def _module_findable(name: str) -> bool:
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError, ModuleNotFoundError):
        return False


class PluginRegistry:
    def __init__(self):
        self._entries: Dict[str, PluginEntry] = {}
        self._bootstrapped = False
        self._state_lock = threading.RLock()
        self._runtime_dir: Optional[Path] = None

    @property
    def bootstrapped(self) -> bool:
        return self._bootstrapped

    def sync_bundled(self):
        if not BUNDLED_PLUGINS_DIR.is_dir():
            return
        if self._runtime_dir is None:
            self._runtime_dir = get_plugins_dir()
        self._runtime_dir.mkdir(parents=True, exist_ok=True)
        for child in sorted(BUNDLED_PLUGINS_DIR.iterdir()):
            if not child.is_dir():
                continue
            manifest = _read_manifest(child / MANIFEST_FILE_NAME)
            pid = manifest.get("id") or child.name
            dst = self._runtime_dir / pid
            bundled_version = manifest.get("version", "")
            installed_version = _read_manifest(dst / MANIFEST_FILE_NAME).get("version", "") if dst.exists() else ""
            if not dst.exists() or (bundled_version and bundled_version != installed_version):
                if dst.exists():
                    shutil.rmtree(dst, ignore_errors=True)
                shutil.copytree(child, dst)
                _log.info("Synced bundled plugin %s (v%s)", pid, bundled_version)

    def discover(self):
        if self._runtime_dir is None:
            self._runtime_dir = get_plugins_dir()
        self._runtime_dir.mkdir(parents=True, exist_ok=True)
        state = self._read_global_state()
        for child in sorted(self._runtime_dir.iterdir()):
            if not child.is_dir():
                continue
            manifest = _read_manifest(child / MANIFEST_FILE_NAME)
            if not manifest:
                continue
            pid = manifest.get("id") or child.name
            if not re.match(r"^[a-zA-Z0-9_-]+$", pid):
                _log.warning("Skipping plugin with invalid id %r", pid)
                continue
            entry = PluginEntry(pid, child, manifest)
            entry.enabled = bool(state.get(pid, {}).get("enabled", True))
            self._entries[pid] = entry

    def bootstrap(self):
        with self._state_lock:
            if self._bootstrapped:
                return
            self._bootstrapped = True
            cfg = ServerConfig.load()
            if not cfg.plugins_enabled:
                return
            self.sync_bundled()
            self.discover()
            self._import_loaded()

    def ensure_bootstrap(self):
        if not self._bootstrapped:
            self.bootstrap()

    def _import_loaded(self):
        for pid in sorted(self._entries):
            entry = self._entries[pid]
            if not entry.enabled:
                continue
            try:
                entry.instance, entry.context = self._import_plugin(entry)
                entry.loaded = True
                entry.error = ""
            except Exception as e:
                _log.exception("Failed to load plugin %s", pid)
                entry.loaded = False
                entry.error = f"{type(e).__name__}: {e}"

    def _import_plugin(self, entry: PluginEntry) -> Tuple[Plugin, PluginContext]:
        self._ensure_requirements(entry)
        mod_name = f"uroboros_plugin_{entry.id}"
        spec = importlib.util.spec_from_file_location(mod_name, entry.dir / PLUGIN_ENTRY_FILE)
        if spec is None or spec.loader is None:
            raise RuntimeError("plugin.py could not be loaded")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        cls = self._find_plugin_class(module)
        if cls is None:
            raise RuntimeError("no Plugin subclass found in plugin.py")
        inst = cls()
        inst._plugin_dir = entry.dir
        ctx = PluginContext(inst, entry.dir, self)
        inst.ctx = ctx
        inst.on_load(ctx)
        return inst, ctx

    @staticmethod
    def _find_plugin_class(module):
        for _, obj in vars(module).items():
            if inspect.isclass(obj) and issubclass(obj, Plugin) and obj is not Plugin:
                return obj
        return None

    def _ensure_requirements(self, entry: PluginEntry):
        missing = [(pkg, mod) for pkg, mod in _dependency_items(entry.manifest)
                   if not _module_findable(mod)]
        if not missing:
            return
        pkgs = [pkg for pkg, _ in missing]
        _log.info("[plugins] auto-installing requirements for %s: %s", entry.id, ", ".join(pkgs))
        proc = self._pip_install(pkgs)
        if proc is None or proc.returncode != 0:
            detail = (proc.stderr or proc.stdout or "")[-800:] if proc else "pip is not available"
            raise RuntimeError(f"failed to auto-install requirements {pkgs}: {detail}")
        still_missing = [mod for _, mod in missing if not _module_findable(mod)]
        if still_missing:
            raise RuntimeError(f"requirements still not importable after install: {still_missing}")

    @staticmethod
    def _pip_install(pkgs: List[str]):
        cmd = [sys.executable, "-m", "pip", "install",
               "--disable-pip-version-check", "--no-input"]
        proc = None
        for attempt in range(2):
            try:
                proc = subprocess.run(cmd + list(pkgs), capture_output=True,
                                      text=True, timeout=900)
            except Exception as e:
                return None if attempt == 0 else None
            if proc.returncode == 0:
                return proc
            if attempt == 0:
                try:
                    subprocess.run([sys.executable, "-m", "ensurepip", "--upgrade"],
                                   capture_output=True, text=True, timeout=180)
                except Exception:
                    pass
        return proc

    def _read_global_state(self) -> dict:
        try:
            raw = json.loads(_PLUGIN_STATE_FILE.read_text(encoding="utf-8"))
            return raw if isinstance(raw, dict) else {}
        except Exception:
            return {}

    def _write_global_state(self, state: dict):
        _PLUGIN_STATE_FILE.write_text(
            json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8"
        )

    def set_enabled(self, plugin_id: str, enabled: bool):
        state = self._read_global_state()
        state.setdefault(plugin_id, {})["enabled"] = bool(enabled)
        self._write_global_state(state)
        entry = self._entries.get(plugin_id)
        if entry:
            entry.enabled = bool(enabled)

    def get_entry(self, plugin_id: str) -> Optional[PluginEntry]:
        return self._entries.get(plugin_id)

    def list_entries(self) -> List[dict]:
        result = []
        for pid in sorted(self._entries):
            entry = self._entries[pid]
            result.append({
                "id": entry.id,
                "name": entry.manifest.get("name", entry.id),
                "version": entry.manifest.get("version", ""),
                "description": entry.manifest.get("description", ""),
                "author": entry.manifest.get("author", ""),
                "homepage": entry.manifest.get("homepage", ""),
                "requirements": [pkg for pkg, _ in _dependency_items(entry.manifest)],
                "enabled": entry.enabled,
                "loaded": entry.loaded,
                "error": entry.error,
                "needs_restart": entry.enabled and not entry.loaded,
            })
        return result

    def get_tabs(self) -> List[TabDef]:
        tabs: List[TabDef] = []
        for pid in sorted(self._entries):
            entry = self._entries[pid]
            if entry.loaded and entry.instance:
                tabs.extend(entry.instance._tabs)
        return tabs

    def get_assets(self) -> Tuple[List[str], List[str]]:
        css: List[str] = []
        js: List[str] = []
        for pid in sorted(self._entries):
            entry = self._entries[pid]
            if not entry.loaded or not entry.instance:
                continue
            base = f"/admin/static/plugins/{pid}"
            for rel in entry.manifest.get("css", []) or []:
                css.append(f"{base}/{rel.lstrip('/')}")
            for rel in entry.manifest.get("js", []) or []:
                js.append(f"{base}/{rel.lstrip('/')}")
            for tab in entry.instance._tabs:
                for rel in tab.css:
                    css.append(f"{base}/{rel.lstrip('/')}")
                for rel in tab.scripts:
                    js.append(f"{base}/{rel.lstrip('/')}")
        return css, js

    def attach_to_app(self, app):
        for pid in sorted(self._entries):
            entry = self._entries[pid]
            if not entry.loaded or not entry.instance:
                continue
            static_dir = entry.dir / "static"
            if static_dir.is_dir():
                app.mount(
                    f"/admin/static/plugins/{pid}",
                    StaticFiles(directory=str(static_dir)),
                    name=f"plugin_static_{pid}",
                )
            for router, prefix in entry.instance._routers:
                app.include_router(router, prefix=prefix)

    def include_admin_routes(self, app):
        app.include_router(plugins_router, prefix="/admin")

    async def startup_all(self, app):
        for pid in sorted(self._entries):
            entry = self._entries[pid]
            if not entry.loaded or not entry.instance:
                continue
            ctx = entry.context
            ctx.app = app
            try:
                await entry.instance.on_startup(ctx)
            except Exception:
                _log.exception("Plugin %s on_startup failed", pid)

    async def shutdown_all(self, app):
        for pid in sorted(self._entries):
            entry = self._entries[pid]
            if not entry.loaded or not entry.instance:
                continue
            try:
                await entry.instance.on_shutdown(entry.context)
            except Exception:
                _log.exception("Plugin %s on_shutdown failed", pid)


registry = PluginRegistry()
bus = EventBus()


def bootstrap():
    registry.bootstrap()


def ensure_bootstrap():
    registry.ensure_bootstrap()


def attach_to_app(app):
    registry.attach_to_app(app)
    registry.include_admin_routes(app)


async def startup_all(app):
    await registry.startup_all(app)


async def shutdown_all(app):
    await registry.shutdown_all(app)


def get_tabs() -> List[TabDef]:
    return registry.get_tabs()


def get_assets():
    return registry.get_assets()


plugins_router = APIRouter(dependencies=[Depends(require_admin)])


@plugins_router.get("/plugins")
async def list_plugins():
    registry.ensure_bootstrap()
    return registry.list_entries()


@plugins_router.post("/plugins/{plugin_id}/enable")
async def enable_plugin(plugin_id: str):
    registry.ensure_bootstrap()
    if registry.get_entry(plugin_id) is None:
        return JSONResponse(content={"error": "Plugin not found"}, status_code=404)
    registry.set_enabled(plugin_id, True)
    return {"ok": True, "id": plugin_id}


@plugins_router.post("/plugins/{plugin_id}/disable")
async def disable_plugin(plugin_id: str):
    registry.ensure_bootstrap()
    if registry.get_entry(plugin_id) is None:
        return JSONResponse(content={"error": "Plugin not found"}, status_code=404)
    registry.set_enabled(plugin_id, False)
    return {"ok": True, "id": plugin_id}
