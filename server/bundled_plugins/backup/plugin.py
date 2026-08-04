import asyncio
import fnmatch
import json
import logging
import os
import shutil
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
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    finished_at: Mapped[datetime] = mapped_column(DateTime, nullable=True, default=None)


def _is_within(child: Path, parent: Path) -> bool:
    try:
        Path(child).resolve().relative_to(Path(parent).resolve())
        return True
    except ValueError:
        return False


class BackupPlugin(Plugin):
    id = "backup"
    name = "Backups"
    version = "1.0.0"

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
        cfg = ctx.config()
        root = (cfg.get("backup_dir") or "").strip()
        self._backup_dir = Path(root) if root else (SERVER_DIR / "backups")
        self._backup_dir.mkdir(parents=True, exist_ok=True)
        self._scheduler_task = asyncio.create_task(self._scheduler_loop())
        ctx.log("info", f"started, backup dir: {self._backup_dir}")

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

    def _rule_filename(self, instance_id: str, trigger: str) -> Path:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        inst_dir = self._backup_dir / instance_id
        inst_dir.mkdir(parents=True, exist_ok=True)
        return inst_dir / f"{instance_id}__{trigger}__{ts}.zip"

    def _build_zip_blocking(self, server_dir, folders, excludes, out_path) -> tuple:
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

        entries = []
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
                entries.append((full, rel))

        manifest = {
            "uroboros": "backup",
            "version": "1.0.0",
            "instance_id": None,
            "server_dir": str(server_dir),
            "created_at": datetime.now().isoformat(),
            "file_count": len(entries),
        }
        with zipfile.ZipFile(str(out_path), "w", zipfile.ZIP_DEFLATED, allowZip64=True) as zf:
            zf.writestr("uroboros-backup.json", json.dumps(manifest, indent=2, ensure_ascii=False))
            for full, rel in entries:
                zf.write(str(full), arcname=rel)
        return str(out_path), len(entries), out_path.stat().st_size

    async def _build_zip(self, server_dir, folders, excludes, out_path) -> tuple:
        return await asyncio.to_thread(self._build_zip_blocking, server_dir, folders, excludes, out_path)

    async def _resolve_instance(self, instance_id: str):
        return await get_instance(instance_id)

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
            )
            session.add(record)
            await session.commit()
            record_id = record.id
        try:
            server_path = Path(server_dir)
            if not server_path.is_dir():
                raise FileNotFoundError(f"Server directory not found: {server_dir}")
            out_path = self._rule_filename(rule.instance_id, trigger)
            zip_path, file_count, size = await self._build_zip(
                server_path, rule.folders, rule.exclude, out_path
            )
            async with ctx.get_session() as session:
                rec = await session.get(BackupRecordModel, record_id)
                rule = await session.get(BackupRuleModel, rule_id)
                rec.status = "ok"
                rec.file_path = zip_path
                rec.file_name = Path(zip_path).name
                rec.size_bytes = size
                rec.file_count = file_count
                rec.finished_at = datetime.now()
                if rule is not None:
                    rule.last_run_at = datetime.now()
                await session.commit()
            await self._apply_retention(rule_id, rule.instance_id)
            return record_id
        except Exception as e:
            _log.exception("[backup] rule %s failed", rule_id)
            async with ctx.get_session() as session:
                rec = await session.get(BackupRecordModel, record_id)
                rec.status = "failed"
                rec.error = str(e)[:2000]
                rec.finished_at = datetime.now()
                await session.commit()
            return None

    async def _run_adhoc(self, instance_id: str, folders, exclude, trigger: str = "manual") -> Optional[int]:
        async with self.ctx.get_session() as session:
            inst = await get_instance(instance_id)
            if inst is None:
                return None
            server_dir = inst.server_dir or inst.id
            record = BackupRecordModel(instance_id=instance_id, trigger=trigger, status="running")
            session.add(record)
            await session.commit()
            record_id = record.id
        try:
            server_path = Path(server_dir)
            if not server_path.is_dir():
                raise FileNotFoundError(f"Server directory not found: {server_dir}")
            out_path = self._rule_filename(instance_id, trigger)
            zip_path, file_count, size = await self._build_zip(server_path, folders, exclude, out_path)
            async with self.ctx.get_session() as session:
                rec = await session.get(BackupRecordModel, record_id)
                rec.status = "ok"
                rec.file_path = zip_path
                rec.file_name = Path(zip_path).name
                rec.size_bytes = size
                rec.file_count = file_count
                rec.finished_at = datetime.now()
                await session.commit()
            return record_id
        except Exception as e:
            _log.exception("[backup] ad-hoc backup failed for %s", instance_id)
            async with self.ctx.get_session() as session:
                rec = await session.get(BackupRecordModel, record_id)
                rec.status = "failed"
                rec.error = str(e)[:2000]
                rec.finished_at = datetime.now()
                await session.commit()
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
                    if _is_within(path, self._backup_dir) and path.is_file():
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
    return {
        "id": rec.id,
        "instance_id": rec.instance_id,
        "rule_id": rec.rule_id,
        "trigger": rec.trigger,
        "status": rec.status,
        "error": rec.error,
        "file_name": rec.file_name,
        "size_bytes": rec.size_bytes,
        "file_count": rec.file_count,
        "created_at": rec.created_at.isoformat() if rec.created_at else None,
        "finished_at": rec.finished_at.isoformat() if rec.finished_at else None,
    }


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
    result = []
    async with plugin.ctx.get_session() as session:
        for inst in instances:
            rules = (await session.execute(
                select(BackupRuleModel).where(BackupRuleModel.instance_id == inst.id)
            )).scalars().all()
            records = (await session.execute(
                select(BackupRecordModel).where(BackupRecordModel.instance_id == inst.id)
            )).scalars().all()
            ok_records = [r for r in records if r.status == "ok"]
            last = None
            if ok_records:
                last = max(ok_records, key=lambda r: r.created_at)
            result.append({
                "id": inst.id,
                "name": inst.name or inst.id,
                "server_dir": inst.server_dir or inst.id,
                "running": is_running(inst.id),
                "rules": len(rules),
                "backups": len(ok_records),
                "total_size": sum(r.size_bytes for r in ok_records),
                "last_backup_at": last.created_at.isoformat() if last else None,
            })
    return result


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
                    "retention_count", "retention_days", "backup_on_stop"):
            if key in body:
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
    record_id = await plugin._run_adhoc(instance_id, folders, exclude, trigger="manual")
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
        records = (await session.execute(select(BackupRecordModel))).scalars().all()
        rules = (await session.execute(select(BackupRuleModel))).scalars().all()
    ok = [r for r in records if r.status == "ok"]
    return {
        "rules": len(rules),
        "records": len(records),
        "backups": len(ok),
        "failed": sum(1 for r in records if r.status == "failed"),
        "total_size": sum(r.size_bytes for r in ok),
        "last_backup_at": max((r.created_at for r in ok), default=None),
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
        if not _is_within(path, plugin._backup_dir) or not path.is_file():
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
        if not _is_within(path, plugin._backup_dir) or not path.is_file():
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
            if _is_within(path, plugin._backup_dir) and path.is_file():
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
