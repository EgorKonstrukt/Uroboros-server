import os
import signal
import time
from pathlib import Path
from typing import Optional

PID_DIR = Path.home() / ".uroboros" / "server" / "pids"


def _ensure():
    PID_DIR.mkdir(parents=True, exist_ok=True)


def _pid_file(instance_id: str) -> Path:
    safe = instance_id.replace("..", "").replace("/", "_").replace("\\", "_")
    return PID_DIR / f"{safe}.pid"


def _pid_alive(pid: int) -> bool:
    if os.name == "nt":
        try:
            import psutil
            return psutil.pid_exists(pid)
        except Exception:
            pass
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _proc_start_time(pid: int) -> Optional[float]:
    try:
        import psutil
        return float(psutil.Process(pid).create_time())
    except Exception:
        return None


def write_pid_for(instance_id: str, pid: int, start_time: Optional[float] = None):
    _ensure()
    if start_time is None:
        start_time = _proc_start_time(pid)
    lines = [str(pid)]
    if start_time is not None:
        lines.append(str(start_time))
    _pid_file(instance_id).write_text("\n".join(lines))


def read_pid_for(instance_id: str) -> Optional[int]:
    pf = _pid_file(instance_id)
    if pf.exists():
        try:
            return int(pf.read_text().strip().splitlines()[0])
        except (ValueError, OSError, IndexError):
            return None
    return None


def _read_start_time(instance_id: str) -> Optional[float]:
    pf = _pid_file(instance_id)
    if pf.exists():
        try:
            lines = pf.read_text().strip().splitlines()
            if len(lines) >= 2:
                return float(lines[1])
        except (ValueError, OSError, IndexError):
            pass
    return None


def _entry_alive(pid: int, start_time: Optional[float]) -> bool:
    if start_time is not None:
        try:
            import psutil
            if not psutil.pid_exists(pid):
                return False
            now_start = psutil.Process(pid).create_time()
            return abs(float(now_start) - start_time) < 5.0
        except Exception:
            pass
    return _pid_alive(pid)


def clear_pid_for(instance_id: str):
    pf = _pid_file(instance_id)
    try:
        pf.unlink(missing_ok=True)
    except OSError:
        pass


def is_running(instance_id: str) -> bool:
    pid = read_pid_for(instance_id)
    if pid is None:
        return False
    start_time = _read_start_time(instance_id)
    if _entry_alive(pid, start_time):
        return True
    clear_pid_for(instance_id)
    return False


def stop_process(instance_id: str, timeout: float = 30) -> bool:
    pid = read_pid_for(instance_id)
    if pid is None:
        return False
    start_time = _read_start_time(instance_id)
    try:
        if not _entry_alive(pid, start_time):
            clear_pid_for(instance_id)
            return True
        os.kill(pid, signal.SIGTERM)
        deadline = time.time() + timeout
        while time.time() < deadline:
            if not _entry_alive(pid, start_time):
                clear_pid_for(instance_id)
                return True
            time.sleep(0.5)
        os.kill(pid, signal.SIGKILL)
        clear_pid_for(instance_id)
        return True
    except OSError:
        clear_pid_for(instance_id)
        return False
