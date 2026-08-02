import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
import zipfile
from datetime import datetime
from pathlib import Path

from server.version import APP_NAME, APP_VERSION

GITHUB_REPO = "EgorKonstrukt/Uroboros-server"
GITHUB_API = f"https://api.github.com/repos/{GITHUB_REPO}"
GITHUB_ARCHIVE = f"https://github.com/{GITHUB_REPO}/archive/refs"
USER_AGENT = f"Uroboros/{APP_VERSION} (update-checker)"
ROOT = Path(__file__).resolve().parent.parent
BACKUP_DIR = ROOT / ".backups"
EXCLUDED = {
    ".venv", ".git", ".backups", "dist", ".build", ".idea", ".playwright-mcp",
    "__pycache__", ".pytest_cache",
}


class UpdateCancelled(Exception):
    pass


def _gh_json(url):
    req = urllib.request.Request(
        url,
        headers={"User-Agent": USER_AGENT, "Accept": "application/vnd.github+json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        if e.code == 403:
            raise RuntimeError("GitHub API rate limit exceeded. Try again later.")
        if e.code == 404:
            return None
        raise RuntimeError(f"GitHub API error (HTTP {e.code})")
    except urllib.error.URLError as e:
        raise RuntimeError(f"Network error: {e.reason}")


def _split_numeric(value):
    return [int(x) for x in re.findall(r"\d+", value or "")]


def compare(a, b):
    pa = _split_numeric(a)
    pb = _split_numeric(b)
    while len(pa) < len(pb):
        pa.append(0)
    while len(pb) < len(pa):
        pb.append(0)
    for x, y in zip(pa, pb):
        if x != y:
            return 1 if x > y else -1
    return 0


def get_latest():
    release = _gh_json(f"{GITHUB_API}/releases/latest")
    if release:
        tag = release.get("tag_name") or ""
        name = tag
        if name[:1].lower() == "v" and name[1:2].isdigit():
            name = name[1:]
        return {
            "kind": "release",
            "version": name or tag,
            "ref": tag,
            "download_url": f"{GITHUB_ARCHIVE}/tags/{tag}.zip",
            "published_at": release.get("published_at") or "",
            "body": (release.get("body") or "").strip(),
        }
    repo = _gh_json(GITHUB_API)
    if not repo:
        raise RuntimeError("Repository not found or inaccessible")
    branch = repo.get("default_branch") or "master"
    commit = _gh_json(f"{GITHUB_API}/commits/{branch}")
    sha = (commit or {}).get("sha") or branch
    return {
        "kind": "branch",
        "version": sha[:7],
        "ref": branch,
        "download_url": f"{GITHUB_ARCHIVE}/heads/{branch}.zip",
        "published_at": "",
        "body": "",
    }


def _local_head():
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True, text=True, cwd=str(ROOT), timeout=10,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except Exception:
        pass
    return None


def _download(url, dest, cancel_event=None):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            with open(dest, "wb") as f:
                while True:
                    if cancel_event is not None and cancel_event.is_set():
                        raise UpdateCancelled()
                    chunk = resp.read(256 * 1024)
                    if not chunk:
                        break
                    f.write(chunk)
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"Download failed (HTTP {e.code}) from {url}")
    except urllib.error.URLError as e:
        raise RuntimeError(f"Network error: {e.reason}")


def _extract_zip(zip_path):
    tmp = Path(tempfile.mkdtemp(prefix="uroboros_update_"))
    with zipfile.ZipFile(zip_path) as zf:
        zf.extractall(tmp)
    roots = [p for p in tmp.iterdir() if p.is_dir()]
    if not roots:
        shutil.rmtree(tmp, ignore_errors=True)
        raise RuntimeError("Archive does not contain a project folder")
    named = [p for p in roots if p.name.lower().startswith("uroboros")]
    return (named[0] if named else roots[0])


def _excluded(rel):
    parts = rel.parts
    if not parts:
        return False
    if any(p in EXCLUDED for p in parts):
        return True
    if any(p.endswith(".log") or p.endswith(".pyc") for p in parts):
        return True
    return False


def _backup():
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    dest = BACKUP_DIR / f"uroboros_{APP_VERSION}_{stamp}.zip"
    with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in ROOT.rglob("*"):
            if not path.is_file():
                continue
            rel = path.relative_to(ROOT)
            if _excluded(rel):
                continue
            zf.write(path, arcname=rel.as_posix())
    return dest


def _sync(src, dst, cancel_event=None):
    copied = 0
    removed = 0
    new_files = set()
    for path in src.rglob("*"):
        if cancel_event is not None and cancel_event.is_set():
            raise UpdateCancelled()
        rel = path.relative_to(src)
        if _excluded(rel):
            continue
        target = dst / rel
        if path.is_dir():
            target.mkdir(parents=True, exist_ok=True)
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=str(target.parent), prefix=".urob_tmp_")
        try:
            with os.fdopen(fd, "wb") as out, open(path, "rb") as inp:
                shutil.copyfileobj(inp, out)
            os.replace(tmp, target)
        except Exception:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
        new_files.add(rel.as_posix())
        copied += 1
    for path in sorted(dst.rglob("*"), reverse=True):
        rel = path.relative_to(dst)
        if _excluded(rel):
            continue
        if path.is_dir():
            try:
                path.rmdir()
            except OSError:
                pass
            continue
        if rel.as_posix() not in new_files:
            try:
                path.unlink()
                removed += 1
            except OSError:
                pass
    return copied, removed


def _read_version(root):
    version_file = root / "server" / "version.py"
    if not version_file.exists():
        return None
    match = re.search(
        r'APP_VERSION\s*=\s*["\']([^"\']+)',
        version_file.read_text(encoding="utf-8", errors="replace"),
    )
    return match.group(1) if match else None


def _install_requirements():
    req = ROOT / "requirements.txt"
    if not req.exists():
        print("[WARN] requirements.txt not found, skipping dependency install.")
        return
    result = subprocess.run([sys.executable, "-m", "pip", "install", "-r", str(req)])
    if result.returncode != 0:
        print("[WARN] Dependency install failed. The launcher will retry on next start.")


def running_server_count():
    try:
        from server.mc.pidfile import PID_DIR, is_running
        if not PID_DIR.exists():
            return 0
        return sum(1 for p in PID_DIR.glob("*.pid") if is_running(p.stem))
    except Exception:
        return 0


def check():
    result = {
        "installed": APP_VERSION,
        "latest": None,
        "update_available": False,
        "reason": "",
        "error": None,
        "checked_at": datetime.now().isoformat(timespec="seconds"),
    }
    try:
        latest = get_latest()
    except RuntimeError as e:
        result["error"] = str(e)
        return result
    result["latest"] = latest
    if latest["kind"] == "release":
        result["update_available"] = compare(latest["version"], APP_VERSION) > 0
        result["reason"] = "release"
    else:
        head = _local_head()
        result["update_available"] = not (bool(head) and head.startswith(latest["version"]))
        result["reason"] = "branch"
    return result


def perform_update(latest, progress=None, cancel_event=None, install_requirements=True):
    def report(message, phase=None, current=None, total=None):
        if progress:
            try:
                progress(message, phase=phase, current=current, total=total)
            except Exception:
                pass

    def cancelled():
        return cancel_event is not None and cancel_event.is_set()

    report("Downloading source archive ...", phase="download")
    tmp = Path(tempfile.mkdtemp(prefix="uroboros_update_"))
    new_version = APP_VERSION
    backup = ""
    try:
        zip_path = tmp / "source.zip"
        _download(latest["download_url"], zip_path, cancel_event=cancel_event)
        if cancelled():
            raise UpdateCancelled()
        report("Extracting archive ...", phase="extract")
        src_root = _extract_zip(zip_path)
        new_version = _read_version(src_root) or APP_VERSION
        report("Backing up current version ...", phase="backup")
        try:
            backup = str(_backup())
            report(f"Backup saved: {backup}", phase="backup")
        except Exception as e:
            report(f"Could not create backup: {e}", phase="backup")
        if cancelled():
            raise UpdateCancelled()
        report("Applying update ...", phase="sync")
        copied, removed = _sync(src_root, ROOT, cancel_event=cancel_event)
        report(f"Updated files: {copied} copied, {removed} removed", phase="sync")
    except UpdateCancelled:
        raise
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    if install_requirements:
        report("Installing requirements ...", phase="requirements")
        _install_requirements()
    return {"new_version": new_version, "backup": backup}


def update(force=False, yes=False, check_only=False):
    print(f"{APP_NAME} {APP_VERSION}")
    print(f"Checking GitHub: {GITHUB_REPO} ...")
    info = check()
    if info["error"]:
        print(f"[ERROR] {info['error']}")
        return 1
    latest = info["latest"]
    print()
    if latest["kind"] == "release":
        print(f"Latest release: {latest['version']}")
    else:
        print(f"Latest on branch {latest['ref']}: {latest['version']}")
    if latest["published_at"]:
        print(f"Published: {latest['published_at']}")

    up_to_date = not info["update_available"]

    if check_only:
        print("Update available." if not up_to_date else "You are up to date.")
        return 0
    if up_to_date and not force:
        print("You are up to date.")
        return 0

    if latest["body"]:
        print()
        print("Release notes:")
        print(latest["body"][:2000])
    running = running_server_count()
    if running:
        print()
        print(f"[WARN] {running} Minecraft server(s) appear to be running.")
        print("       Stop them before updating to avoid data loss.")
    print()
    if not yes:
        try:
            answer = input(f"Update to {latest['version']}? [y/N]: ").strip().lower()
        except EOFError:
            answer = ""
        if answer not in ("y", "yes"):
            print("Update cancelled.")
            return 0

    print()
    try:
        result = perform_update(
            latest,
            progress=lambda msg, phase=None, current=None, total=None: print(msg),
            install_requirements=True,
        )
    except UpdateCancelled:
        print("[INFO] Update cancelled.")
        return 0
    except RuntimeError as e:
        print(f"[ERROR] {e}")
        return 1
    print()
    print(f"[OK] Updated to {result['new_version']}. Restart the server to apply the update.")
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description="Update Uroboros Server from GitHub")
    parser.add_argument("--check", action="store_true", help="Only check for updates")
    parser.add_argument("--force", action="store_true", help="Apply even if version is the same")
    parser.add_argument("--yes", action="store_true", help="Skip the confirmation prompt")
    args = parser.parse_args(argv)
    return update(force=args.force, yes=args.yes, check_only=args.check)


if __name__ == "__main__":
    sys.exit(main())
