import os
import re
import asyncio
import platform
import shutil
import subprocess
import sys
import threading
import uuid
import zipfile
from pathlib import Path
from dataclasses import dataclass
from typing import Callable, List, Optional

from server.config import SERVER_DIR


JAVA_DIR = SERVER_DIR / "java"


@dataclass
class JavaRuntime:
    path: str
    version: str
    major_version: int
    vendor: str
    arch: str
    source: str = ""


_JAVA_CACHE: List[JavaRuntime] = []
_JAVA_CACHE_LOCK = threading.Lock()


def get_cached() -> List[JavaRuntime]:
    with _JAVA_CACHE_LOCK:
        return list(_JAVA_CACHE)


def _refresh_cache(merge_keep_installed: bool = True) -> None:
    global _JAVA_CACHE
    found: dict[str, JavaRuntime] = {}
    installed_prefix = str(JAVA_DIR.resolve())

    for path in _find_candidates():
        if path in found:
            continue
        jr = _probe_java(path)
        if jr:
            if str(Path(path).resolve()).startswith(installed_prefix):
                jr.source = "installed"
            found[jr.path] = jr

    if merge_keep_installed:
        with _JAVA_CACHE_LOCK:
            for jr in list(_JAVA_CACHE):
                if jr.path not in found and Path(jr.path).exists():
                    found[jr.path] = jr

    with _JAVA_CACHE_LOCK:
        _JAVA_CACHE = sorted(found.values(), key=lambda r: r.major_version, reverse=True)


def scan_java() -> List[JavaRuntime]:
    _refresh_cache(merge_keep_installed=True)
    return get_cached()


def _probe_java(path: str) -> Optional[JavaRuntime]:
    try:
        r = subprocess.run(
            [path, "-version"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        output = r.stderr + r.stdout

        m = re.search(r'version\s+"([^"]+)"', output)
        if not m:
            return None
        version = m.group(1)

        major_match = re.match(r"(\d+)", version)
        if not major_match:
            return None
        major = int(major_match.group(1))
        if major == 1:
            m2 = re.match(r"1\.(\d+)", version)
            if m2:
                major = int(m2.group(1))

        vendor = _detect_vendor(output)
        arch = "64-bit" if re.search(r"64[-\s]?[Bb]it|amd64|x86_64", output) else "32-bit"
        source = ""

        return JavaRuntime(
            path=str(Path(path).resolve()),
            version=version,
            major_version=major,
            vendor=vendor,
            arch=arch,
            source=source,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return None


def _detect_vendor(output: str) -> str:
    if re.search(r"Temurin|Eclipse Adoptium|AdoptOpenJDK", output):
        return "Adoptium"
    if "Java(TM)" in output or ("Oracle" in output and "OpenJDK" not in output):
        return "Oracle JDK"
    if "OpenJDK" in output:
        return "OpenJDK"
    if "Amazon" in output or "Corretto" in output:
        return "Amazon Corretto"
    if "Microsoft" in output:
        return "Microsoft"
    if "GraalVM" in output:
        return "GraalVM"
    if "IBM" in output:
        return "IBM"
    if "BellSoft" in output or "Liberica" in output:
        return "Liberica"
    if "Zulu" in output or "Azul" in output:
        return "Azul Zulu"
    if "SAP" in output:
        return "SAP"
    if "Red Hat" in output:
        return "Red Hat"
    return "Unknown"


def _find_candidates() -> List[str]:
    seen: set[str] = set()
    candidates: List[str] = []

    def add(p: str):
        if p and p not in seen:
            seen.add(p)
            candidates.append(p)

    for exe in ("java.exe", "java"):
        for d in os.environ.get("PATH", "").split(os.pathsep):
            if not d.strip():
                continue
            p = Path(d.strip()) / exe
            if p.exists():
                add(str(p.resolve()))

    java_home = os.environ.get("JAVA_HOME", "")
    if java_home:
        for exe in ("java.exe", "java"):
            p = Path(java_home) / "bin" / exe
            if p.exists():
                add(str(p.resolve()))

    if JAVA_DIR.exists():
        for exe in ("java.exe", "java"):
            for sub in sorted(JAVA_DIR.iterdir()):
                if sub.is_dir():
                    p = sub / "bin" / exe
                    if p.exists():
                        add(str(p.resolve()))

    if sys.platform == "win32":
        for base in (
            os.environ.get("ProgramFiles", "C:\\Program Files"),
            os.environ.get("ProgramFiles(x86)", "C:\\Program Files (x86)"),
        ):
            base_p = Path(base)
            if not base_p.exists():
                continue
            for vendor_dir in [
                "Eclipse Adoptium",
                "Adoptium",
                "Java",
                "Amazon Corretto",
                "Microsoft JDK",
                "BellSoft",
                "LibericaJDK",
                "Zulu",
                "GraalVM",
            ]:
                vdir = base_p / vendor_dir
                if vdir.exists():
                    for sub in sorted(vdir.iterdir()):
                        jp = sub / "bin" / "java.exe"
                        if jp.exists():
                            add(str(jp.resolve()))

        try:
            import winreg

            for root in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
                for subkey in (
                    r"SOFTWARE\JavaSoft\Java Development Kit",
                    r"SOFTWARE\JavaSoft\Java Runtime Environment",
                    r"SOFTWARE\Adoptium\JDK",
                    r"SOFTWARE\Eclipse Foundation\JDK",
                ):
                    try:
                        with winreg.OpenKey(root, subkey) as k:
                            try:
                                cv = winreg.QueryValueEx(k, "CurrentVersion")[0]
                                with winreg.OpenKey(k, cv) as vk:
                                    jh = winreg.QueryValueEx(vk, "JavaHome")[0]
                                    p = Path(jh) / "bin" / "java.exe"
                                    if p.exists():
                                        add(str(p.resolve()))
                            except OSError:
                                pass
                            i = 0
                            while True:
                                try:
                                    sk_name = winreg.EnumKey(k, i)
                                    with winreg.OpenKey(k, sk_name) as sk:
                                        jh = winreg.QueryValueEx(sk, "JavaHome")[0]
                                        p = Path(jh) / "bin" / "java.exe"
                                        if p.exists():
                                            add(str(p.resolve()))
                                    i += 1
                                except OSError:
                                    break
                    except OSError:
                        continue
        except ImportError:
            pass

    return candidates


def resolve_java_path(desired: str, detected: Optional[List[JavaRuntime]] = None) -> str:
    if not detected:
        detected = _JAVA_CACHE
    for jr in detected:
        if jr.path == desired or jr.version == desired or str(jr.major_version) == desired:
            return jr.path
    return desired


def get_platform() -> dict:
    machine = platform.machine().lower()
    system = platform.system().lower()
    arch_map = {
        "amd64": "x64",
        "x86_64": "x64",
        "x86": "x86",
        "i386": "x86",
        "i686": "x86",
        "armv7l": "arm",
        "aarch64": "aarch64",
        "arm64": "aarch64",
    }
    arch = arch_map.get(machine, machine or "x64")
    os_map = {"windows": "windows", "linux": "linux", "darwin": "mac"}
    os_name = os_map.get(system, system or "windows")
    return {"os": os_name, "arch": arch, "display": f"{system} / {arch}"}


def get_installed() -> List[JavaRuntime]:
    runtimes = []
    if JAVA_DIR.exists():
        for child in sorted(JAVA_DIR.iterdir()):
            if not child.is_dir():
                continue
            jb = _find_java_bin(child)
            if not jb:
                continue
            jr = _probe_java(str(jb))
            if jr:
                jr.source = "installed"
                runtimes.append(jr)
    return runtimes


def _find_java_bin(root: Path) -> Optional[Path]:
    for exe in ("bin/java.exe", "bin/java"):
        p = root / exe
        if p.exists():
            return p
    for child in root.iterdir():
        if child.is_dir():
            for exe in ("bin/java.exe", "bin/java"):
                p = child / exe
                if p.exists():
                    return p
    return None


def _installed_root(path: str) -> Optional[Path]:
    resolved = Path(path).resolve()
    base = JAVA_DIR.resolve()
    for parent in resolved.parents:
        if parent.parent == base:
            return parent
    return None


def uninstall_java(path: str) -> bool:
    root = _installed_root(path)
    if root is None or not root.exists():
        return False
    shutil.rmtree(root, ignore_errors=True)
    prefix = str(root.resolve())
    global _JAVA_CACHE
    with _JAVA_CACHE_LOCK:
        _JAVA_CACHE = [j for j in _JAVA_CACHE if not str(Path(j.path).resolve()).startswith(prefix)]
    return True


async def list_available_versions() -> list:
    import aiohttp
    async with aiohttp.ClientSession() as session:
        async with session.get(
            "https://api.adoptium.net/v3/info/available_releases",
            timeout=aiohttp.ClientTimeout(total=30),
        ) as resp:
            if resp.status != 200:
                raise RuntimeError(f"Failed to fetch available releases (HTTP {resp.status})")
            data = await resp.json()
    return data.get("available_releases", [])


_VENDOR_META = {
    "temurin": {"label": "Eclipse Temurin", "versions": None},
    "zulu": {"label": "Azul Zulu", "versions": None},
    "microsoft": {"label": "Microsoft OpenJDK", "versions": [11, 17, 21]},
    "corretto": {"label": "Amazon Corretto", "versions": [8, 11, 17, 21]},
}


async def get_vendors() -> list:
    try:
        adoptium_versions = await list_available_versions()
    except Exception:
        adoptium_versions = [8, 11, 16, 17, 18, 19, 20, 21, 22, 23, 24, 25, 26]
    result = []
    for vid, meta in _VENDOR_META.items():
        versions = meta["versions"] if meta["versions"] else adoptium_versions
        result.append({"id": vid, "label": meta["label"], "versions": versions})
    return result


async def _probe_download_size(url: str) -> Optional[int]:
    import aiohttp
    async with aiohttp.ClientSession() as session:
        try:
            async with session.head(url, allow_redirects=True, timeout=aiohttp.ClientTimeout(total=60)) as resp:
                if resp.status != 200:
                    return None
                return int(resp.headers.get("Content-Length") or 0)
        except Exception:
            return None


async def _adoptium_latest(version: int, plat: dict) -> dict:
    import aiohttp
    url = f"https://api.adoptium.net/v3/assets/latest/{version}/hotspot"
    params = {"architecture": plat["arch"], "image_type": "jdk", "os": plat["os"], "project": "jdk"}
    async with aiohttp.ClientSession() as session:
        async with session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=60)) as resp:
            if resp.status != 200:
                raise RuntimeError(f"Temurin Java {version} not available for {plat['display']} (HTTP {resp.status})")
            data = await resp.json()
    if not data:
        raise RuntimeError(f"No Temurin Java {version} build available for {plat['display']}")
    pkg = data[0]["binary"]["package"]
    return {
        "url": pkg["link"],
        "size": int(pkg.get("size") or 0),
        "name": pkg["name"],
        "version": data[0]["version"]["semver"],
        "vendor": "Temurin",
    }


async def _zulu_latest(version: int, plat: dict) -> dict:
    import aiohttp
    os_map = {"windows": "windows", "linux": "linux", "mac": "macos"}
    os_name = os_map.get(plat["os"], plat["os"])
    ext = "zip" if os_name == "windows" else "tar.gz"
    params = {
        "java_version": str(version),
        "os": os_name,
        "arch": plat["arch"],
        "archive_type": ext,
        "java_package_type": "jdk",
        "javafx_bundled": "false",
        "latest": "true",
        "release_status": "ga",
    }
    async with aiohttp.ClientSession() as session:
        async with session.get(
            "https://api.azul.com/metadata/v1/zulu/packages",
            params=params,
            timeout=aiohttp.ClientTimeout(total=60),
        ) as resp:
            if resp.status != 200:
                raise RuntimeError(f"Zulu Java {version} not available for {plat['display']} (HTTP {resp.status})")
            data = await resp.json()
    if not data:
        raise RuntimeError(f"No Zulu Java {version} build available for {plat['display']}")
    items = [p for p in data if "musl" not in (p.get("name") or "")]
    item = items[0] if items else data[0]
    url = item["download_url"]
    return {
        "url": url,
        "size": int(item.get("size") or 0),
        "name": item.get("name") or url.split("/")[-1],
        "version": str(version),
        "vendor": "Azul Zulu",
    }


async def _microsoft_latest(version: int, plat: dict) -> dict:
    if plat["arch"] not in ("x64", "aarch64"):
        raise RuntimeError(f"Microsoft OpenJDK is not available for {plat['arch']}")
    os_map = {"windows": "windows", "linux": "linux", "mac": "macos"}
    os_name = os_map.get(plat["os"], plat["os"])
    ext = "zip" if os_name == "windows" else "tar.gz"
    filename = f"microsoft-jdk-{version}-{os_name}-{plat['arch']}.{ext}"
    url = f"https://aka.ms/download-jdk/{filename}"
    size = await _probe_download_size(url)
    if size is None:
        raise RuntimeError(f"Microsoft OpenJDK {version} is not available for {plat['display']}")
    return {"url": url, "size": size, "name": filename, "version": str(version), "vendor": "Microsoft"}


async def _corretto_latest(version: int, plat: dict) -> dict:
    if plat["arch"] not in ("x64", "aarch64"):
        raise RuntimeError(f"Amazon Corretto is not available for {plat['arch']}")
    os_map = {"windows": "windows", "linux": "linux", "mac": "macos"}
    os_name = os_map.get(plat["os"], plat["os"])
    if os_name == "windows" and plat["arch"] == "aarch64":
        raise RuntimeError("Amazon Corretto does not provide a Windows ARM64 build")
    ext = "zip" if os_name == "windows" else "tar.gz"
    filename = f"amazon-corretto-{version}-{plat['arch']}-{os_name}-jdk.{ext}"
    url = f"https://corretto.aws/downloads/latest/{filename}"
    size = await _probe_download_size(url)
    if size is None:
        raise RuntimeError(f"Amazon Corretto {version} is not available for {plat['display']}")
    return {"url": url, "size": size, "name": filename, "version": str(version), "vendor": "Corretto"}


_VENDOR_PROVIDERS = {
    "temurin": _adoptium_latest,
    "zulu": _zulu_latest,
    "microsoft": _microsoft_latest,
    "corretto": _corretto_latest,
}


async def _latest_for_vendor(vendor: str, version: int, plat: dict) -> dict:
    fn = _VENDOR_PROVIDERS.get(vendor)
    if fn is None:
        raise RuntimeError(f"Unknown vendor: {vendor}")
    return await fn(version, plat)


async def _download_file(session, url: str, dest: Path, total: int, cb):
    import aiohttp
    async with session.get(url, timeout=aiohttp.ClientTimeout(total=3600)) as resp:
        if resp.status != 200:
            raise RuntimeError(f"Download failed (HTTP {resp.status})")
        size = total or int(resp.headers.get("Content-Length") or 0)
        downloaded = 0
        with open(dest, "wb") as f:
            async for chunk in resp.content.iter_chunked(256 * 1024):
                f.write(chunk)
                downloaded += len(chunk)
                if cb:
                    cb({"status": "downloading", "current": downloaded, "total": size,
                        "message": f"Downloading {downloaded // 1048576} MB / {max(size, 1) // 1048576} MB"})
        if cb:
            cb({"status": "downloading", "current": size, "total": size, "message": "Download complete"})


def _extract_archive(path: Path, dest: Path, is_zip: bool, cb):
    if is_zip:
        with zipfile.ZipFile(path) as zf:
            members = zf.infolist()
            total = len(members)
            for i, member in enumerate(members):
                try:
                    zf.extract(member, dest, filter="data")
                except TypeError:
                    zf.extract(member, dest)
                if cb and i % 20 == 0:
                    cb({"status": "extracting", "current": i, "total": total,
                        "message": f"Extracting {i}/{total}"})
            if cb:
                cb({"status": "extracting", "current": total, "total": total, "message": "Extraction complete"})
    else:
        import tarfile
        with tarfile.open(path, "r:*") as tf:
            members = tf.getmembers()
            total = len(members)
            for i, member in enumerate(members):
                try:
                    tf.extract(member, dest, filter="tar")
                except TypeError:
                    tf.extract(member, dest)
                if cb and i % 20 == 0:
                    cb({"status": "extracting", "current": i, "total": total,
                        "message": f"Extracting {i}/{total}"})
            if cb:
                cb({"status": "extracting", "current": total, "total": total, "message": "Extraction complete"})


async def install_java(
    version: int,
    vendor: str = "temurin",
    progress_callback: Optional[Callable[[dict], None]] = None,
) -> JavaRuntime:
    plat = get_platform()
    import aiohttp

    asset = await _latest_for_vendor(vendor, version, plat)
    download_url = asset["url"]
    total_size = asset["size"]
    is_zip = download_url.endswith(".zip")

    async with aiohttp.ClientSession() as session:
        JAVA_DIR.mkdir(parents=True, exist_ok=True)
        tmp_root = JAVA_DIR / f"__tmp_{uuid.uuid4().hex}"
        tmp_root.mkdir(parents=True)
        archive_path = tmp_root / f"jdk{version}{'.zip' if is_zip else '.tar.gz'}"
        try:
            if progress_callback:
                progress_callback({"status": "downloading", "current": 0, "total": total_size,
                                   "message": f"Resolving {asset['name']}..."})
            await _download_file(session, download_url, archive_path, total_size, progress_callback)
            await asyncio.to_thread(_extract_archive, archive_path, tmp_root, is_zip, progress_callback)
            jb = _find_java_bin(tmp_root)
            if jb is None:
                raise RuntimeError("Installed JDK does not contain a java binary")
            root_dir = jb.parent.parent
            final_name = root_dir.name or f"jdk-{version}"
            dest = JAVA_DIR / final_name
            if dest.exists():
                dest = JAVA_DIR / f"{final_name}-{uuid.uuid4().hex[:6]}"
            shutil.move(str(root_dir), str(dest))
        finally:
            shutil.rmtree(tmp_root, ignore_errors=True)

    jb = _find_java_bin(dest)
    jr = _probe_java(str(jb))
    if jr is None:
        raise RuntimeError("Failed to probe installed java binary")
    jr.source = "installed"
    global _JAVA_CACHE
    with _JAVA_CACHE_LOCK:
        _JAVA_CACHE = [j for j in _JAVA_CACHE if j.path != jr.path]
        _JAVA_CACHE.append(jr)
    return jr
