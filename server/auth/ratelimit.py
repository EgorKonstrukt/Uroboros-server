import threading
import time
from collections import defaultdict, deque


class RateLimiter:
    def __init__(self, max_hits: int = 10, window_seconds: float = 60.0):
        self.max_hits = max_hits
        self.window_seconds = window_seconds
        self._hits: dict[str, deque] = defaultdict(deque)
        self._lock = threading.Lock()

    def allow(self, key: str) -> bool:
        now = time.monotonic()
        with self._lock:
            bucket = self._hits[key]
            while bucket and bucket[0] < now - self.window_seconds:
                bucket.popleft()
            if len(bucket) >= self.max_hits:
                return False
            bucket.append(now)
            return True

    def remaining(self, key: str) -> int:
        now = time.monotonic()
        with self._lock:
            bucket = self._hits.get(key)
            if not bucket:
                return self.max_hits
            while bucket and bucket[0] < now - self.window_seconds:
                bucket.popleft()
            return max(0, self.max_hits - len(bucket))

    def clear(self, key: str):
        with self._lock:
            self._hits.pop(key, None)


def _limiter_params(kind: str):
    from server.config import ServerConfig

    cfg = ServerConfig.load()
    if kind == "auth":
        return int(getattr(cfg, "auth_limiter_max_hits", 10)), float(getattr(cfg, "auth_limiter_window_seconds", 60.0))
    return int(getattr(cfg, "login_limiter_max_hits", 5)), float(getattr(cfg, "login_limiter_window_seconds", 300.0))


_auth_max, _auth_window = _limiter_params("auth")
auth_limiter = RateLimiter(max_hits=_auth_max, window_seconds=_auth_window)
_login_max, _login_window = _limiter_params("login")
login_limiter = RateLimiter(max_hits=_login_max, window_seconds=_login_window)
