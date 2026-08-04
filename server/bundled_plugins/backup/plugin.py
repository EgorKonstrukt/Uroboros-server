import asyncio
import fnmatch
import json
import logging
import os
import shutil
import threading
import zipfile
from collections import deque
from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy import String, JSON, DateTime, func, Integer, Boolean, Text

from server.models import Base
from server.mc.registry import get_instance, get_manager
from server.web.auth import require_admin

from sqlalchemy.orm import Mapped, mapped_column

from server.config import SERVER_DIR
from server.plugins import Plugin, TabDef, bus

_log = logging.getLogger("uroboros")


class BackupRuleModel(Base):
    __tablename__ = "backup_rules"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    instance_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(255), default="", nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    folders: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    exclude: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    interval_seconds: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    retention_count: Mapped[int] = mapped_column(Integer, default=10, nullable=False)
    retention_days: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    backup_on_stop: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    destination_dir: Mapped[str] = mapped_column(String(1024), default="", nullable=False)
    last_run_at: Mapped[datetime] = mapped_column(DateTime, nullable=True, default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())


class BackupRecordModel(Base):
    __tablename__ = "backup_records"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    instance_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    rule_id: Mapped[int] = mapped_column(Integer, nullable=True, default=None, index=True)
    trigger: Mapped[str] = mapped_column(String(16), default="manual", nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="running", nullable=False)
    error: Mapped[str] = mapped_column(Text, default="", nullable=False)
    file_name: Mapped[str] = mapped_column(String(512), default="", nullable=False)
    file_path: Mapped[str] = mapped_column(String(1024), default="", nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    file_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    destination_dir: Mapped[str] = mapped_column(String(1024), default="", nullable=False)
    progress_percent: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    total_bytes: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    processed_bytes: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    finished_at: Mapped[datetime] = mapped_column(DateTime, nullable=True, default=None)


_PROGRESS: dict = {}
_PROGRESS_LOCK = threading.Lock()


def _set_progress(record_id: int, data: dict):
    with _PROGRESS_LOCK:
        _PROGRESS[record_id] = dict(data)


def _get_progress(record_id: int) -> Optional[dict]:
    with _PROGRESS_LOCK:
        data = _PROGRESS.get(record_id)
        return dict(data) if data else None


def _clear_progress(record_id: int):
    with _PROGRESS_LOCK:
        _PROGRESS.pop(record_id, None)


def _update_progress(record_id: int, done_files: int, done_bytes: int):
    with _PROGRESS_LOCK:
        data = _PROGRESS.get(record_id)
        if data is None:
            return
        data["done_files"] = done_files
        data["done_bytes"] = done_bytes
        total_b = data.get("total_bytes") or 0
        total_f = data.get("total_files") or 0
        if total_b > 0:
            data["percent"] = max(0, min(100, int(done_bytes * 100 / total_b)))
        elif total_f > 0:
            data["percent"] = max(0, min(100, int(done_files * 100 / total_f)))
        else:
            data["percent"] = 0


def _is_within(child: Path, parent: Path) -> bool:
    try:
        Path(child).resolve().relative_to(Path(parent).resolve())
        return True
    except ValueError:
        return False


def _in_storage(path, destination_dir: str, backup_dir: Path) -> bool:
    path = Path(path)
    if _is_within(path, backup_dir):
        return True
    dest = (destination_dir or "").strip()
    if dest:
        try:
            return _is_within(path, Path(dest))
        except Exception:
            return False
    return False


class BackupPlugin(Plugin):
    id = "backup"
    name = "Backups"
    version = "1.1.0"

    def __init__(self):
        super().__init__()
        self._backup_dir: Path = SERVER_DIR / "backups"
        self._stop_queue: deque = deque()
        self._scheduler_task: Optional[asyncio.Task] = None
        self._scheduler_interval = 30

    # ── registration ──

    def on_load(self, ctx):
        bus.on("instance:stopped", self._on_instance_stopped)
        self.register_router(backup_router, prefix="/admin/backups")
        self.register_tab(TabDef(
            id="backups",
            title="Backups",
            group="GENERAL",
            order=70,
            fragment="backups.html",
            loader="loadBackups",
        ))
        ctx.log("info", "loaded")

    # ── lifecycle ──

    async def on_startup(self, ctx):
        await self._migrate_tables()
        cfg = ctx.config()
        root = (cfg.get("backup_dir") or "").strip()
        self._backup_dir = Path(root) if root else (SERVER_DIR / "backups")
        self._backup_dir.mkdir(parents=True, exist_ok=True)
        self._scheduler_task = asyncio.create_task(self._scheduler_loop())
        ctx.log("info", f"started, backup dir: {self._backup_dir}")

    async def _migrate_tables(self):
        from sqlalchemy import text

        schema = {
            "backup_rules": {"destination_dir": "VARCHAR(1024) NOT NULL DEFAULT ''"},
            "backup_records": {
                "destination_dir": "VARCHAR(1024) NOT NULL DEFAULT ''",
                "progress_percent": "INTEGER NOT NULL DEFAULT 0",
                "total_bytes": "INTEGER NOT NULL DEFAULT 0",
                "processed_bytes": "INTEGER NOT NULL DEFAULT 0",
            },
        }
        try:
            async with self.ctx.get_session() as session:
                for table, cols in schema.items():
                    existing = {row[1] for row in
                                (await session.execute(text(f"PRAGMA table_info({table})"))).fetchall()}
                    for col, ddl in cols.items():
                        if col not in existing:
                            await session.execute(
                                text(f"ALTER TABLE {table} ADD COLUMN {col} {ddl}")
                            )
                await session.commit()
        except Exception:
            _log.exception("[backup] schema migration failed")

    async def on_shutdown(self, ctx):
        if self._scheduler_task:
            self._scheduler_task.cancel()
            try:
                await self._scheduler_task
            except asyncio.CancelledError:
                pass
        ctx.log("info", "stopped")

    # ── event handler (runs in a daemon thread) ──

    def _on_instance_stopped(self, instance_id=None):
        self._stop_queue.append(instance_id)

    # ── scheduler ──

    async def _scheduler_loop(self):
        while True:
            try:
                await asyncio.sleep(self._scheduler_interval)
                await self._tick()
            except asyncio.CancelledError:
                break
            except Exception:
                _log.exception("[backup] scheduler tick failed")

    async def _tick(self):
        while self._stop_queue:
            instance_id = self._stop_queue.popleft()
            try:
                await self._backup_on_stop_for(instance_id)
            except Exception:
                _log.exception("[backup] on-stop backup failed for %s", instance_id)
        now = datetime.now()
        due = []
        async with self.ctx.get_session() as session:
            result = await session.execute(
                select(BackupRuleModel).where(BackupRuleModel.enabled == True)  # noqa: E712
            )
            for rule in result.scalars().all():
                if rule.interval_seconds <= 0:
                    continue
                if rule.last_run_at is None:
                    due.append(rule.id)
                else:
                    elapsed = (now - rule.last_run_at).total_seconds()
                    if elapsed >= rule.interval_seconds:
                        due.append(rule.id)
        for rule_id in due:
            await self._run_rule(rule_id, trigger="schedule")

    async def _backup_on_stop_for(self, instance_id):
        async with self.ctx.get_session() as session:
            result = await session.execute(
                select(BackupRuleModel).where(
                    BackupRuleModel.instance_id == instance_id,
                    BackupRuleModel.enabled == True,  # noqa: E712
                    BackupRuleModel.backup_on_stop == True,  # noqa: E712
                )
            )
            rule_ids = [r.id for r in result.scalars().all()]
        for rule_id in rule_ids:
            await self._run_rule(rule_id, trigger="on_stop")

    # ── backup engine ──

    def _out_path(self, instance_id: str, trigger: str, destination_dir: str = "") -> Path:
        base = Path(destination_dir.strip()) if (destination_dir or "").strip() else self._backup_dir
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        inst_dir = base / instance_id
        inst_dir.mkdir(parents=True, exist_ok=True)
        return inst_dir / f"{instance_id}__{trigger}__{ts}.zip"

    def _build_zip_blocking(self, server_dir, folders, excludes, out_path, record_id=None) -> tuple:
        server_dir = Path(server_dir).resolve()
        out_path = Path(out_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)

        includes = [p.strip().replace("\\", "/").rstrip("/") for p in (folders or ["."]) if p and p.strip()]
        if not includes:
            includes = ["."]
        excludes = [p.strip() for p in (excludes or []) if p and p.strip()]

        def included(rel: str) -> bool:
            for inc in includes:
                if inc == ".":
                    return True
                if rel == inc or rel.startswith(inc + "/"):
                    return True
            return False

        def excluded(rel: str) -> bool:
            for pat in excludes:
                if fnmatch.fnmatch(rel, pat) or fnmatch.fnmatch(rel, "*/" + pat):
                    return True
            return False

        def under_backup(path: Path) -> bool:
            return _is_within(path, self._backup_dir)

        if record_id:
            _set_progress(record_id, {
                "phase": "scan", "percent": 0, "total_files": 0,
                "done_files": 0, "total_bytes": 0, "done_bytes": 0,
            })
        entries = []
        total_bytes = 0
        for dirpath, dirnames, filenames in os.walk(server_dir):
            dir_p = Path(dirpath)
            dirnames[:] = [d for d in dirnames if not under_backup(dir_p / d)]
            for fname in filenames:
                full = dir_p / fname
                if under_backup(full):
                    continue
                rel = full.relative_to(server_dir).as_posix()
                if not included(rel) or excluded(rel):
                    continue
                try:
                    size = full.stat().st_size
                except OSError:
                    size = 0
                total_bytes += size
                entries.append((full, rel, size))

        if record_id:
            _set_progress(record_id, {
                "phase": "zip", "percent": 0, "total_files": len(entries),
                "done_files": 0, "total_bytes": total_bytes, "done_bytes": 0,
            })

        manifest = {
            "uroboros": "backup",
            "version": "1.1.0",
            "instance_id": None,
            "server_dir": str(server_dir),
            "created_at": datetime.now().isoformat(),
            "file_count": len(entries),
        }
        try:
            with zipfile.ZipFile(str(out_path), "w", zipfile.ZIP_DEFLATED, allowZip64=True) as zf:
                zf.writestr("uroboros-backup.json", json.dumps(manifest, indent=2, ensure_ascii=False))
                done_bytes = 0
                for idx, (full, rel, size) in enumerate(entries):
                    zf.write(str(full), arcname=rel)
                    done_bytes += size
                    if record_id and (idx % 4 == 0 or idx == len(entries) - 1):
                        _update_progress(record_id, idx + 1, done_bytes)
        finally:
            if record_id:
                _clear_progress(record_id)
        return str(out_path), len(entries), out_path.stat().st_size

    async def _build_zip(self, server_dir, folders, excludes, out_path, record_id=None) -> tuple:
        return await asyncio.to_thread(
            self._build_zip_blocking, server_dir, folders, excludes, out_path, record_id
        )

    async def _resolve_instance(self, instance_id: str):
        return await get_instance(instance_id)

    async def _finish_record(self, record_id, status, **kw):
        async with self.ctx.get_session() as session:
            rec = await session.get(BackupRecordModel, record_id)
            if rec is None:
                return
            rec.status = status
            rec.finished_at = datetime.now()
            for key, value in kw.items():
                setattr(rec, key, value)
            await session.commit()

    async def _run_rule(self, rule_id: int, trigger: str = "manual") -> Optional[int]:
        ctx = self.ctx
        async with ctx.get_session() as session:
            rule = await session.get(BackupRuleModel, rule_id)
            if rule is None or not rule.enabled:
                return None
            inst = await get_instance(rule.instance_id)
            if inst is None:
                return None
            server_dir = inst.server_dir or inst.id
            record = BackupRecordModel(
                instance_id=rule.instance_id,
                rule_id=rule_id,
                trigger=trigger,
                status="running",
                destination_dir=rule.destination_dir or "",
            )
            session.add(record)
            await session.commit()
            record_id = record.id
        try:
            server_path = Path(server_dir)
            if not server_path.is_dir():
                raise FileNotFoundError(f"Server directory not found: {server_dir}")
            out_path = self._out_path(rule.instance_id, trigger, rule.destination_dir or "")
            zip_path, file_count, size = await self._build_zip(
                server_path, rule.folders, rule.exclude, out_path, record_id
            )
            await self._finish_record(record_id, "ok",
                                      file_path=zip_path,
                                      file_name=Path(zip_path).name,
                                      size_bytes=size,
                                      file_count=file_count,
                                      progress_percent=100,
                                      total_bytes=size,
                                      processed_bytes=size)
            async with ctx.get_session() as session:
                r = await session.get(BackupRuleModel, rule_id)
                if r is not None:
                    r.last_run_at = datetime.now()
                    await session.commit()
            await self._apply_retention(rule_id, rule.instance_id)
            return record_id
        except Exception as e:
            _log.exception("[backup] rule %s failed", rule_id)
            await self._finish_record(record_id, "failed", error=str(e)[:2000])
            return None

    async def _run_adhoc(self, instance_id: str, folders, exclude, trigger: str = "manual",
                         destination_dir: str = "") -> Optional[int]:
        ctx = self.ctx
        async with ctx.get_session() as session:
            inst = await get_instance(instance_id)
            if inst is None:
                return None
            server_dir = inst.server_dir or inst.id
            record = BackupRecordModel(
                instance_id=instance_id, trigger=trigger, status="running",
                destination_dir=(destination_dir or "").strip(),
            )
            session.add(record)
            await session.commit()
            record_id = record.id
        try:
            server_path = Path(server_dir)
            if not server_path.is_dir():
                raise FileNotFoundError(f"Server directory not found: {server_dir}")
            out_path = self._out_path(instance_id, trigger, (destination_dir or "").strip())
            zip_path, file_count, size = await self._build_zip(server_path, folders, exclude, out_path, record_id)
            await self._finish_record(record_id, "ok",
                                      file_path=zip_path,
                                      file_name=Path(zip_path).name,
                                      size_bytes=size,
                                      file_count=file_count,
                                      progress_percent=100,
                                      total_bytes=size,
                                      processed_bytes=size)
            return record_id
        except Exception as e:
            _log.exception("[backup] ad-hoc backup failed for %s", instance_id)
            await self._finish_record(record_id, "failed", error=str(e)[:2000])
            return None

    async def _apply_retention(self, rule_id: int, instance_id: str):
        async with self.ctx.get_session() as session:
            rule = await session.get(BackupRuleModel, rule_id)
            if rule is None:
                return
            if rule.retention_count <= 0 and rule.retention_days <= 0:
                return
            stmt = select(BackupRecordModel).where(
                BackupRecordModel.instance_id == instance_id,
                BackupRecordModel.rule_id == rule_id,
                BackupRecordModel.status == "ok",
            ).order_by(BackupRecordModel.created_at.desc())
            records = (await session.execute(stmt)).scalars().all()
            keep = rule.retention_count if rule.retention_count > 0 else len(records)
            cutoff = None
            if rule.retention_days > 0:
                from datetime import timedelta
                cutoff = datetime.now() - timedelta(days=rule.retention_days)
            doomed = []
            for idx, rec in enumerate(records):
                if idx >= keep:
                    doomed.append(rec)
                elif cutoff is not None and rec.created_at and rec.created_at < cutoff:
                    doomed.append(rec)
            for rec in doomed:
                try:
                    path = Path(rec.file_path)
                    if _in_storage(path, rec.destination_dir, self._backup_dir) and path.is_file():
                        path.unlink(missing_ok=True)
                except Exception:
                    pass
                await session.delete(rec)
            await session.commit()

    async def _apply_retention_all(self):
        async with self.ctx.get_session() as session:
            rule_ids = [r.id for r in (await session.execute(select(BackupRuleModel))).scalars().all()]
        for rid in rule_ids:
            try:
                async with self.ctx.get_session() as session:
                    rule = await session.get(BackupRuleModel, rid)
                    if rule is None:
                        continue
                    instance_id = rule.instance_id
                await self._apply_retention(rid, instance_id)
            except Exception:
                _log.exception("[backup] retention pass failed for rule %s", rid)


_backup_router = APIRouter(dependencies=[Depends(require_admin)])


def _plugin():
    from server.plugins import registry
    entry = registry.get_entry("backup")
    return entry.instance if entry and entry.loaded else None


def _record_to_dict(rec) -> dict:
    d = {
        "id": rec.id,
        "instance_id": rec.instance_id,
        "rule_id": rec.rule_id,
        "trigger": rec.trigger,
        "status": rec.status,
        "error": rec.error,
        "file_name": rec.file_name,
        "size_bytes": rec.size_bytes,
        "file_count": rec.file_count,
        "destination_dir": rec.destination_dir or "",
        "progress_percent": rec.progress_percent or 0,
        "total_bytes": rec.total_bytes or 0,
        "processed_bytes": rec.processed_bytes or 0,
        "created_at": rec.created_at.isoformat() if rec.created_at else None,
        "finished_at": rec.finished_at.isoformat() if rec.finished_at else None,
    }
    if rec.status == "running":
        live = _get_progress(rec.id)
        if live:
            d["progress_percent"] = live.get("percent", d["progress_percent"])
            d["total_bytes"] = live.get("total_bytes", d["total_bytes"])
            d["processed_bytes"] = live.get("done_bytes", d["processed_bytes"])
            d["done_files"] = live.get("done_files", 0)
            d["total_files"] = live.get("total_files", 0)
            d["phase"] = live.get("phase", "")
    return d


def _rule_to_dict(rule) -> dict:
    return {
        "id": rule.id,
        "instance_id": rule.instance_id,
        "name": rule.name,
        "enabled": rule.enabled,
        "folders": rule.folders or [],
        "exclude": rule.exclude or [],
        "interval_seconds": rule.interval_seconds,
        "retention_count": rule.retention_count,
        "retention_days": rule.retention_days,
        "backup_on_stop": rule.backup_on_stop,
        "destination_dir": rule.destination_dir or "",
        "last_run_at": rule.last_run_at.isoformat() if rule.last_run_at else None,
    }


@_backup_router.get("/instances")
async def list_backup_instances():
    from server.mc.registry import load_instances
    from server.mc.pidfile import is_running

    plugin = _plugin()
    if plugin is None:
        return JSONResponse(content={"error": "Plugin not loaded"}, status_code=500)
    instances = await load_instances()
    ids = [inst.id for inst in instances]
    rule_map = {}
    record_map = {}
    async with plugin.ctx.get_session() as session:
        if ids:
            for row in (await session.execute(
                select(BackupRuleModel.instance_id, func.count(BackupRuleModel.id))
                .where(BackupRuleModel.instance_id.in_(ids))
                .group_by(BackupRuleModel.instance_id)
            )).all():
                rule_map[row[0]] = row[1]
            for row in (await session.execute(
                select(
                    BackupRecordModel.instance_id,
                    func.count(BackupRecordModel.id),
                    func.sum(BackupRecordModel.size_bytes),
                    func.max(BackupRecordModel.created_at),
                )
                .where(
                    BackupRecordModel.instance_id.in_(ids),
                    BackupRecordModel.status == "ok",
                )
                .group_by(BackupRecordModel.instance_id)
            )).all():
                record_map[row[0]] = (row[1], row[2] or 0, row[3])
    result = []
    for inst in instances:
        rec = record_map.get(inst.id)
        result.append({
            "id": inst.id,
            "name": inst.name or inst.id,
            "server_dir": inst.server_dir or inst.id,
            "running": is_running(inst.id),
            "rules": rule_map.get(inst.id, 0),
            "backups": rec[0] if rec else 0,
            "total_size": rec[1] if rec else 0,
            "last_backup_at": rec[2].isoformat() if rec and rec[2] else None,
        })
    return result


@_backup_router.get("/instances/{instance_id}/tree")
async def instance_folder_tree(instance_id: str, path: str = ""):
    plugin = _plugin()
    if plugin is None:
        return JSONResponse(content={"error": "Plugin not loaded"}, status_code=500)
    inst = await get_instance(instance_id)
    if inst is None:
        return JSONResponse(content={"error": "Instance not found"}, status_code=404)
    server_dir = Path(inst.server_dir or inst.id)
    try:
        base = server_dir.resolve()
    except Exception:
        base = server_dir.absolute()
    rel = Path(path or "").as_posix().lstrip("/")
    rel_path = Path(rel) if rel else Path(".")
    if rel_path.is_absolute() or ".." in rel_path.parts:
        return JSONResponse(content={"error": "Invalid path"}, status_code=400)
    try:
        cur = (base / rel_path).resolve()
    except Exception:
        cur = base
    if not _is_within(cur, base):
        return JSONResponse(content={"error": "Invalid path"}, status_code=400)
    dirs = []
    file_count = 0
    if cur.is_dir():
        try:
            with os.scandir(str(cur)) as it:
                for entry in it:
                    try:
                        if entry.is_dir(follow_symlinks=False):
                            if _is_within(Path(entry.path), plugin._backup_dir):
                                continue
                            sub = (rel_path / entry.name) if rel else Path(entry.name)
                            dirs.append({
                                "name": entry.name,
                                "path": sub.as_posix(),
                                "file_count": _direct_file_count(Path(entry.path)),
                            })
                        elif entry.is_file(follow_symlinks=False):
                            file_count += 1
                    except OSError:
                        continue
        except OSError:
            pass
        dirs.sort(key=lambda d: d["name"].lower())
    return {"root": rel if rel else "", "dirs": dirs, "file_count": file_count}


def _direct_file_count(path: Path) -> int:
    count = 0
    try:
        with os.scandir(str(path)) as it:
            for entry in it:
                try:
                    if entry.is_file(follow_symlinks=False):
                        count += 1
                    elif entry.is_dir(follow_symlinks=False):
                        pass
                except OSError:
                    continue
    except OSError:
        pass
    return count


@_backup_router.get("/progress/{record_id}")
async def backup_progress(record_id: int):
    plugin = _plugin()
    if plugin is None:
        return JSONResponse(content={"error": "Plugin not loaded"}, status_code=500)
    live = _get_progress(record_id)
    if live is not None:
        return {"running": True, **live}
    async with plugin.ctx.get_session() as session:
        rec = await session.get(BackupRecordModel, record_id)
        if rec is None:
            return JSONResponse(content={"error": "Backup not found"}, status_code=404)
        return {
            "running": rec.status == "running",
            "phase": "done",
            "percent": rec.progress_percent or (100 if rec.status == "ok" else 0),
            "total_files": rec.file_count,
            "done_files": rec.file_count if rec.status == "ok" else 0,
            "total_bytes": rec.total_bytes,
            "done_bytes": rec.processed_bytes if rec.status == "ok" else 0,
        }


@_backup_router.get("/rules")
async def list_rules(instance_id: str = ""):
    plugin = _plugin()
    if plugin is None:
        return JSONResponse(content={"error": "Plugin not loaded"}, status_code=500)
    async with plugin.ctx.get_session() as session:
        stmt = select(BackupRuleModel)
        if instance_id:
            stmt = stmt.where(BackupRuleModel.instance_id == instance_id)
        stmt = stmt.order_by(BackupRuleModel.id)
        rules = (await session.execute(stmt)).scalars().all()
        return [_rule_to_dict(r) for r in rules]


@_backup_router.post("/rules")
async def create_rule(body: dict):
    plugin = _plugin()
    if plugin is None:
        return JSONResponse(content={"error": "Plugin not loaded"}, status_code=500)
    instance_id = str(body.get("instance_id", "")).strip()
    if not instance_id:
        return JSONResponse(content={"error": "instance_id is required"}, status_code=400)
    inst = await get_instance(instance_id)
    if inst is None:
        return JSONResponse(content={"error": "Instance not found"}, status_code=404)
    rule = BackupRuleModel(
        instance_id=instance_id,
        name=str(body.get("name", ""))[:255],
        enabled=bool(body.get("enabled", True)),
        folders=list(body.get("folders") or []) or ["."],
        exclude=list(body.get("exclude") or []),
        interval_seconds=int(body.get("interval_seconds") or 0),
        retention_count=int(body.get("retention_count") or 0),
        retention_days=int(body.get("retention_days") or 0),
        backup_on_stop=bool(body.get("backup_on_stop", False)),
        destination_dir=str(body.get("destination_dir") or "").strip()[:1024],
    )
    async with plugin.ctx.get_session() as session:
        session.add(rule)
        await session.commit()
        await session.refresh(rule)
        return _rule_to_dict(rule)


@_backup_router.put("/rules/{rule_id}")
async def update_rule(rule_id: int, body: dict):
    plugin = _plugin()
    if plugin is None:
        return JSONResponse(content={"error": "Plugin not loaded"}, status_code=500)
    async with plugin.ctx.get_session() as session:
        rule = await session.get(BackupRuleModel, rule_id)
        if rule is None:
            return JSONResponse(content={"error": "Rule not found"}, status_code=404)
        for key in ("name", "enabled", "folders", "exclude", "interval_seconds",
                    "retention_count", "retention_days", "backup_on_stop", "destination_dir"):
            if key in body:
                if key == "destination_dir":
                    rule.destination_dir = str(body[key] or "").strip()[:1024]
                else:
                    setattr(rule, key, body[key])
        await session.commit()
        await session.refresh(rule)
        return _rule_to_dict(rule)


@_backup_router.delete("/rules/{rule_id}")
async def delete_rule(rule_id: int):
    plugin = _plugin()
    if plugin is None:
        return JSONResponse(content={"error": "Plugin not loaded"}, status_code=500)
    async with plugin.ctx.get_session() as session:
        rule = await session.get(BackupRuleModel, rule_id)
        if rule is None:
            return JSONResponse(content={"error": "Rule not found"}, status_code=404)
        await session.delete(rule)
        await session.commit()
    return {"ok": True}


@_backup_router.post("/rules/{rule_id}/run")
async def run_rule(rule_id: int):
    plugin = _plugin()
    if plugin is None:
        return JSONResponse(content={"error": "Plugin not loaded"}, status_code=500)
    record_id = await plugin._run_rule(rule_id, trigger="manual")
    if record_id is None:
        return JSONResponse(content={"error": "Rule is disabled or instance is missing"}, status_code=400)
    return {"ok": True, "record_id": record_id}


@_backup_router.post("/instances/{instance_id}/backup")
async def backup_instance_now(instance_id: str, body: dict):
    plugin = _plugin()
    if plugin is None:
        return JSONResponse(content={"error": "Plugin not loaded"}, status_code=500)
    folders = list(body.get("folders") or []) or ["."]
    exclude = list(body.get("exclude") or [])
    destination = str(body.get("destination_dir") or "").strip()
    record_id = await plugin._run_adhoc(instance_id, folders, exclude, trigger="manual",
                                        destination_dir=destination)
    if record_id is None:
        return JSONResponse(content={"error": "Instance not found"}, status_code=404)
    return {"ok": True, "record_id": record_id}


@_backup_router.get("")
async def list_backups(instance_id: str = "", limit: int = 200):
    plugin = _plugin()
    if plugin is None:
        return JSONResponse(content={"error": "Plugin not loaded"}, status_code=500)
    async with plugin.ctx.get_session() as session:
        stmt = select(BackupRecordModel)
        if instance_id:
            stmt = stmt.where(BackupRecordModel.instance_id == instance_id)
        stmt = stmt.order_by(BackupRecordModel.created_at.desc()).limit(min(max(limit, 1), 1000))
        records = (await session.execute(stmt)).scalars().all()
        return [_record_to_dict(r) for r in records]


@_backup_router.get("/stats")
async def backup_stats():
    plugin = _plugin()
    if plugin is None:
        return JSONResponse(content={"error": "Plugin not loaded"}, status_code=500)
    async with plugin.ctx.get_session() as session:
        total = (await session.execute(
            select(func.count(BackupRecordModel.id))
        )).scalar() or 0
        failed = (await session.execute(
            select(func.count(BackupRecordModel.id)).where(BackupRecordModel.status == "failed")
        )).scalar() or 0
        ok_rows = (await session.execute(
            select(func.count(BackupRecordModel.id), func.sum(BackupRecordModel.size_bytes),
                   func.max(BackupRecordModel.created_at))
            .where(BackupRecordModel.status == "ok")
        )).one()
        rules = (await session.execute(
            select(func.count(BackupRuleModel.id))
        )).scalar() or 0
    return {
        "rules": rules,
        "records": total,
        "backups": ok_rows[0] or 0,
        "failed": failed,
        "total_size": ok_rows[1] or 0,
        "last_backup_at": ok_rows[2],
    }


@_backup_router.get("/{record_id}/download")
async def download_backup(record_id: int):
    from fastapi.responses import FileResponse

    plugin = _plugin()
    if plugin is None:
        return JSONResponse(content={"error": "Plugin not loaded"}, status_code=500)
    async with plugin.ctx.get_session() as session:
        rec = await session.get(BackupRecordModel, record_id)
        if rec is None or not rec.file_path:
            return JSONResponse(content={"error": "Backup not found"}, status_code=404)
        path = Path(rec.file_path)
        if not _in_storage(path, rec.destination_dir, plugin._backup_dir) or not path.is_file():
            return JSONResponse(content={"error": "Backup file is missing"}, status_code=404)
        return FileResponse(str(path), media_type="application/zip",
                            filename=rec.file_name or path.name)


@_backup_router.post("/{record_id}/restore")
async def restore_backup(record_id: int, body: dict):
    plugin = _plugin()
    if plugin is None:
        return JSONResponse(content={"error": "Plugin not loaded"}, status_code=500)
    async with plugin.ctx.get_session() as session:
        rec = await session.get(BackupRecordModel, record_id)
        if rec is None or not rec.file_path:
            return JSONResponse(content={"error": "Backup not found"}, status_code=404)
        path = Path(rec.file_path)
        if not _in_storage(path, rec.destination_dir, plugin._backup_dir) or not path.is_file():
            return JSONResponse(content={"error": "Backup file is missing"}, status_code=404)
        instance_id = rec.instance_id
    inst = await get_instance(instance_id)
    if inst is None:
        return JSONResponse(content={"error": "Instance not found"}, status_code=404)
    server_dir = inst.server_dir or inst.id
    mgr = await get_manager(instance_id)
    should_stop = bool(body.get("stop", False))
    should_start = bool(body.get("start", False))
    if should_stop and mgr is not None and mgr.is_running():
        mgr.stop()
    if should_stop and mgr is not None and mgr.is_running():
        for _ in range(100):
            if not mgr.is_running():
                break
            await asyncio.sleep(0.2)
    try:
        restored = await asyncio.to_thread(
            _restore_zip_blocking, path, server_dir
        )
    except Exception as e:
        return JSONResponse(content={"error": f"Restore failed: {e}"}, status_code=500)
    if should_start and mgr is not None:
        mgr.start()
    return {"ok": True, "restored": restored}


@_backup_router.delete("/{record_id}")
async def delete_backup(record_id: int):
    plugin = _plugin()
    if plugin is None:
        return JSONResponse(content={"error": "Plugin not loaded"}, status_code=500)
    async with plugin.ctx.get_session() as session:
        rec = await session.get(BackupRecordModel, record_id)
        if rec is None:
            return JSONResponse(content={"error": "Backup not found"}, status_code=404)
        try:
            path = Path(rec.file_path)
            if _in_storage(path, rec.destination_dir, plugin._backup_dir) and path.is_file():
                path.unlink(missing_ok=True)
        except Exception:
            pass
        await session.delete(rec)
        await session.commit()
    return {"ok": True}


def _restore_zip_blocking(zip_path, server_dir) -> int:
    server_dir = Path(server_dir).resolve()
    server_dir.mkdir(parents=True, exist_ok=True)
    restored = 0
    with zipfile.ZipFile(str(zip_path), "r") as zf:
        for info in zf.infolist():
            name = info.filename
            if not name or name == "uroboros-backup.json" or name.endswith("/"):
                continue
            rel = Path(name)
            if rel.is_absolute() or ".." in rel.parts:
                raise ValueError(f"Unsafe path in archive: {name!r}")
            dest = (server_dir / rel).resolve()
            if not _is_within(dest, server_dir):
                raise ValueError(f"Unsafe path in archive: {name!r}")
            dest.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(info) as src, open(dest, "wb") as out:
                shutil.copyfileobj(src, out)
            restored += 1
    return restored


backup_router = _backup_router
