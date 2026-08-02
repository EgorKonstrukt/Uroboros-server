import threading
import time
from collections import deque
from pathlib import Path

import aiohttp

USER_AGENT = "Uroboros/1.0"


def format_bytes(n: int) -> str:
    if n >= 1024 * 1024:
        return f"{n / 1024 / 1024:.1f} MB"
    if n >= 1024:
        return f"{n / 1024:.1f} KB"
    return f"{n} B"


def format_speed(bytes_per_sec: float) -> str:
    if bytes_per_sec >= 1024 * 1024:
        return f"{bytes_per_sec / 1024 / 1024:.1f} MB/s"
    if bytes_per_sec >= 1024:
        return f"{bytes_per_sec / 1024:.1f} KB/s"
    return f"{bytes_per_sec:.0f} B/s"


class DownloadCancelled(Exception):
    pass


class DownloadHandle:
    def __init__(self):
        self._cancelled = False
        self._lock = threading.Lock()
        self.state = {
            "status": "starting",
            "current": 0,
            "total": 0,
            "speed": 0,
            "message": "Starting...",
            "cancelled": False,
            "error": "",
        }
        self._samples: deque = deque()

    def cancel(self) -> None:
        with self._lock:
            self._cancelled = True
            self.state["cancelled"] = True
            self.state["status"] = "cancelling"
            self.state["message"] = "Cancelling..."

    @property
    def cancelled(self) -> bool:
        with self._lock:
            return self._cancelled

    def update(self, **kwargs) -> None:
        with self._lock:
            self.state.update(kwargs)

    def snapshot(self) -> dict:
        with self._lock:
            return dict(self.state)

    def _record_sample(self, now: float, total_bytes: int):
        self._samples.append((now, total_bytes))
        while self._samples and now - self._samples[0][0] > 5.0:
            self._samples.popleft()

    def _speed(self, now: float) -> float:
        if len(self._samples) < 2:
            return 0.0
        first_t, first_b = self._samples[0]
        last_t, last_b = self._samples[-1]
        dt = last_t - first_t
        if dt <= 0:
            return 0.0
        return (last_b - first_b) / dt

    async def download(self, url: str, dest: Path) -> None:
        dest.parent.mkdir(parents=True, exist_ok=True)
        try:
            await self._download_impl(url, dest)
        except DownloadCancelled:
            try:
                dest.unlink()
            except OSError:
                pass
            raise

    async def _download_impl(self, url: str, dest: Path):
        async with aiohttp.ClientSession(headers={"User-Agent": USER_AGENT}) as session:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=3600)) as resp:
                if resp.status != 200:
                    raise RuntimeError(f"Download failed (HTTP {resp.status}) from {url}")
                total = int(resp.headers.get("Content-Length") or 0)
                downloaded = 0
                with open(dest, "wb") as f:
                    async for chunk in resp.content.iter_chunked(256 * 1024):
                        if self.cancelled:
                            raise DownloadCancelled()
                        f.write(chunk)
                        downloaded += len(chunk)
                        now = time.monotonic()
                        self._record_sample(now, downloaded)
                        speed = self._speed(now)
                        self.update(
                            status="downloading",
                            current=downloaded,
                            total=total,
                            speed=int(speed),
                            message=f"Downloading {format_bytes(downloaded)} / {format_bytes(total or 0)} \u00b7 {format_speed(speed)}",
                        )
                self.update(
                    status="downloading",
                    current=total,
                    total=total,
                    speed=0,
                    message="Download complete",
                )
