import sys
import os
import argparse
import asyncio
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def cmd_run(args):
    from server.plugins import bootstrap as bootstrap_plugins
    bootstrap_plugins()
    from server.app import app
    from server.database import init_db, close_db
    from server.config import ServerConfig
    import uvicorn

    cfg = ServerConfig.load()
    if cfg.admin_password:
        if cfg.admin_password_plain:
            print(f"[Uroboros] Admin panel password: {cfg.admin_password_plain}")
        else:
            print("[Uroboros] Admin panel password: (set manually, not stored in plaintext)")
    db_path = Path(cfg.db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(init_db(db_path))

        from server.mc.registry import migrate_instances_from_json
        migrate_instances_from_json()

        uvicorn_config = uvicorn.Config(
            app,
            host=args.host or cfg.host,
            port=args.port or cfg.port,
            log_level=args.log_level or cfg.log_level,
            loop="asyncio",
            ssl_certfile=cfg.ssl_certfile or None,
            ssl_keyfile=cfg.ssl_keyfile or None,
            headers=[("server", "Uroboros")],
        )
        server = uvicorn.Server(uvicorn_config)
        loop.run_until_complete(server.serve())
    except KeyboardInterrupt:
        pass
    finally:
        loop.run_until_complete(close_db())
        loop.close()


async def _list_projects():
    from server.database import get_session
    from server.models import ProjectModel
    from sqlalchemy import select
    async with get_session() as session:
        stmt = select(ProjectModel).order_by(ProjectModel.name)
        result = await session.execute(stmt)
        projects = result.scalars().all()
        if not projects:
            print("No projects configured")
            return
        print(f"Projects ({len(projects)}):")
        print(f"{'ID':<20} {'Name':<30} {'MC'}")
        print("-" * 70)
        for p in projects:
            print(f"{p.id:<20} {p.name:<30}")


def cmd_projects_list(args):
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(_list_projects())
    finally:
        loop.close()


def _run_async(coro):
    from server.database import init_db, close_db
    from server.config import ServerConfig
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        db_path = Path(ServerConfig.load().db_path)
        db_path.parent.mkdir(parents=True, exist_ok=True)
        loop.run_until_complete(init_db(db_path))
        from server.mc.registry import migrate_instances_from_json
        migrate_instances_from_json()
        loop.run_until_complete(coro)
    finally:
        try:
            loop.run_until_complete(close_db())
        except Exception:
            pass
        loop.close()


async def _load_instances(instance_id):
    from server.mc.registry import load_instances
    instances = await load_instances()
    if instance_id:
        instances = [i for i in instances if i.id == instance_id]
    if not instances:
        print("No matching instances found")
    return instances


def _instance_address(inst):
    from server.web import _server_address
    return _server_address(inst)


async def _start_instances(instance_id):
    from server.mc.registry import get_manager
    instances = await _load_instances(instance_id)
    for inst in instances:
        if instance_id is None and not inst.enabled:
            print(f"[{inst.id}] {inst.name}: disabled, skipping")
            continue
        mgr = await get_manager(inst.id)
        if mgr is None:
            continue
        if mgr.is_running():
            print(f"[{inst.id}] {inst.name}: already running (pid {mgr.process.pid})")
            continue
        try:
            started = mgr.start()
        except Exception as e:
            print(f"[{inst.id}] {inst.name}: FAILED - {e}")
            continue
        if started:
            print(f"[{inst.id}] {inst.name}: started (pid {mgr.process.pid})")
        else:
            print(f"[{inst.id}] {inst.name}: FAILED - {mgr.last_error or 'unknown error'}")


async def _stop_instances(instance_id):
    from server.mc.pidfile import stop_process
    instances = await _load_instances(instance_id)
    for inst in instances:
        if stop_process(inst.id):
            print(f"[{inst.id}] {inst.name}: stopped")
        else:
            print(f"[{inst.id}] {inst.name}: not running")


async def _show_status(instance_id):
    from server.mc.pidfile import is_running, read_pid_for
    from server.mc.status import probe
    instances = await _load_instances(instance_id)
    for inst in instances:
        running = is_running(inst.id)
        pid = read_pid_for(inst.id)
        if running:
            print(f"[{inst.id}] {inst.name}: running (pid {pid})")
            host, port = _instance_address(inst)
            info = probe(host, port)
            if info.get("online"):
                print(f"  online: {info.get('players_online', 0)}/{info.get('players_max', 0)} players, "
                      f"{info.get('latency_ms', 0)} ms")
            else:
                print(f"  no response on {host}:{port}")
        else:
            print(f"[{inst.id}] {inst.name}: stopped")


def cmd_start(args):
    _run_async(_start_instances(args.instance_id))


def cmd_stop(args):
    _run_async(_stop_instances(args.instance_id))


def cmd_status(args):
    _run_async(_show_status(args.instance_id))


def cmd_update(args):
    from server.updater import main as updater_main
    argv = []
    if args.check:
        argv.append("--check")
    if args.force:
        argv.append("--force")
    if args.yes:
        argv.append("--yes")
    sys.exit(updater_main(argv))


def cmd_version(args):
    from server.version import APP_VERSION
    print(APP_VERSION)


def cmd_set_admin_password(args):
    from server.config import ServerConfig
    from server.auth.crypto import hash_password
    password = args.password
    if password is None:
        if os.name == "nt":
            import msvcrt
            while msvcrt.kbhit():
                msvcrt.getwch()
        while not password:
            try:
                password = input("Enter new admin password: ").strip()
            except EOFError:
                break
            if not password:
                print("[WARN] Password cannot be empty.")
    if not password:
        print("[ERROR] Password cannot be empty.")
        sys.exit(1)
    cfg = ServerConfig.load()
    cfg.admin_password = hash_password(password)
    cfg.admin_password_plain = password
    cfg.save()
    print("[OK] Admin panel password has been set.")


def main():
    parser = argparse.ArgumentParser(description="Uroboros Server")
    sub = parser.add_subparsers(dest="command", required=True)

    p_run = sub.add_parser("run", help="Run server (auth + API + admin)")
    p_run.add_argument("--host", default=None)
    p_run.add_argument("--port", type=int, default=None)
    p_run.add_argument("--log-level", default=None,
                       choices=["critical", "error", "warning", "info", "debug"])
    p_run.set_defaults(func=cmd_run)

    p_projects = sub.add_parser("projects", help="List projects")
    p_projects.set_defaults(func=cmd_projects_list)

    p_start = sub.add_parser("start", help="Start Minecraft server(s)")
    p_start.add_argument("instance_id", nargs="?", default=None)
    p_start.set_defaults(func=cmd_start)

    p_stop = sub.add_parser("stop", help="Stop Minecraft server(s)")
    p_stop.add_argument("instance_id", nargs="?", default=None)
    p_stop.set_defaults(func=cmd_stop)

    p_status = sub.add_parser("status", help="Show Minecraft server status")
    p_status.add_argument("instance_id", nargs="?", default=None)
    p_status.set_defaults(func=cmd_status)

    p_version = sub.add_parser("version", help="Show Uroboros Server version")
    p_version.set_defaults(func=cmd_version)

    p_pass = sub.add_parser("set-admin-password", help="Set the admin panel password (interactive if no password given)")
    p_pass.add_argument("password", nargs="?")
    p_pass.set_defaults(func=cmd_set_admin_password)

    p_update = sub.add_parser("update", help="Update Uroboros Server from GitHub")
    p_update.add_argument("--check", action="store_true", help="Only check for updates")
    p_update.add_argument("--force", action="store_true", help="Apply even if version is the same")
    p_update.add_argument("--yes", action="store_true", help="Skip the confirmation prompt")
    p_update.set_defaults(func=cmd_update)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
