import json
import re
import secrets
import sys
from pathlib import Path
from dataclasses import dataclass, asdict

from server.auth.crypto import hash_password


SERVER_DIR = Path.home() / ".uroboros" / "server"
CONFIG_FILE = SERVER_DIR / "config.json"

_WEAK_PASSWORDS = {"", "blabla", "admin", "password"}


def _resolve_db_path(value: str) -> str:
    if sys.platform != "win32":
        if "\\" in value or re.match(r"^[A-Za-z]:[\\/]", value):
            return str(SERVER_DIR / "auth.db")
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = SERVER_DIR / path
    return str(path)


@dataclass
class ServerConfig:
    host: str = "0.0.0.0"
    port: int = 25581
    public_url: str = ""
    db_path: str = ""
    admin_password: str = ""
    admin_password_plain: str = ""
    log_level: str = "info"
    curseforge_api_key: str = ""
    ssl_certfile: str = ""
    ssl_keyfile: str = ""
    stats_refresh_seconds: int = 2
    console_refresh_ms: int = 500
    trust_proxy_headers: bool = False
    token_expiry_hours: int = 24
    access_token_ttl_hours: int = 24
    auth_limiter_max_hits: int = 10
    auth_limiter_window_seconds: int = 60
    login_limiter_max_hits: int = 5
    login_limiter_window_seconds: int = 300
    max_skin_size_mb: int = 10
    tps_probe_interval: float = 20.0
    status_probe_timeout: float = 3.0
    server_stop_timeout: float = 30.0
    servers_dir: str = ""
    projects_dir: str = ""
    java_dir: str = ""
    injector_dir: str = ""

    def save(self):
        SERVER_DIR.mkdir(parents=True, exist_ok=True)
        data = asdict(self)
        data.pop("admin_password_plain", None)
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

    @classmethod
    def load(cls):
        inst = cls()
        if not CONFIG_FILE.exists():
            inst._ensure_secure_password()
            inst.db_path = str(SERVER_DIR / "auth.db")
            inst.save()
            return inst

        with open(CONFIG_FILE, "r", encoding="utf-8-sig") as f:
            raw = json.load(f)

        raw.pop("admin_password_plain", None)

        for k, v in raw.items():
            if hasattr(inst, k):
                setattr(inst, k, v)

        if not inst.db_path:
            inst.db_path = str(SERVER_DIR / "auth.db")
        else:
            inst.db_path = _resolve_db_path(inst.db_path)

        if inst.admin_password in _WEAK_PASSWORDS:
            inst._ensure_secure_password()
            inst.save()

        if inst.admin_password and not inst.admin_password.startswith("scrypt$"):
            # Re-hash a password that was stored in plaintext before hashing was added
            plain = inst.admin_password
            inst.admin_password = hash_password(plain)
            inst.admin_password_plain = inst.admin_password_plain or plain
            inst.save()

        return inst

    def _ensure_secure_password(self):
        generated = secrets.token_urlsafe(12)
        self.admin_password = hash_password(generated)
        self.admin_password_plain = generated
        print(f"[Uroboros] Generated admin panel password: {generated}")


def _resolve_dir(value: str, default: Path) -> Path:
    if not value:
        return default
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = SERVER_DIR / path
    return path


def get_servers_dir() -> Path:
    return _resolve_dir(ServerConfig.load().servers_dir, SERVER_DIR / "servers")


def get_projects_dir() -> Path:
    return _resolve_dir(ServerConfig.load().projects_dir, SERVER_DIR / "projects")


def get_java_dir() -> Path:
    return _resolve_dir(ServerConfig.load().java_dir, SERVER_DIR / "java")


def get_injector_dir() -> Path:
    return _resolve_dir(ServerConfig.load().injector_dir, SERVER_DIR / "injector")
