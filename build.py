import argparse
import importlib.util
import os
import platform
import shutil
import struct
import subprocess
import sys
import uuid
import zlib
from pathlib import Path

for stream in (sys.stdout, sys.stderr):
    try:
        stream.reconfigure(encoding="utf-8")
    except Exception:
        pass

ROOT = Path(__file__).resolve().parent
DIST_DIR = ROOT / "dist"

sys.path.insert(0, str(ROOT))
from server.version import APP_NAME, APP_VERSION

_arch = platform.machine().lower()
if _arch in ("amd64", "x86_64"):
    ARCH = "x64"
elif _arch in ("arm64", "aarch64"):
    ARCH = "arm64"
else:
    ARCH = _arch
OS_TAG = {"win32": "win", "linux": "linux", "darwin": "macos"}.get(sys.platform, sys.platform)
PLATFORM_TAG = f"{OS_TAG}-{ARCH}"
EXE_NAME = APP_NAME.replace(" ", "")


def _confirm(question, default=False):
    suffix = " (Y/n)" if default else " (y/N)"
    while True:
        raw = input(f"{question}{suffix} ").strip().lower()
        if not raw:
            return default
        if raw in ("y", "yes"):
            return True
        if raw in ("n", "no"):
            return False
        print("Please answer 'y' or 'n'.")


def _ask(question, default):
    raw = input(f"{question} [{default}]: ").strip()
    return raw or default


def _ask_int(question, default):
    while True:
        raw = input(f"{question} [{default}]: ").strip()
        if not raw:
            return default
        try:
            return int(raw)
        except ValueError:
            print("Please enter an integer.")


def _interactive(args):
    print("=" * 60)
    print(f"Building {APP_NAME} v{APP_VERSION} ({PLATFORM_TAG})")
    print("=" * 60)
    print()
    args.onefile = _confirm("Build a single EXE file? (no = onedir folder)", False)
    args.clean = _confirm("Clean the output folder before building?", True)
    args.jobs = _ask_int("Number of parallel jobs (0 = auto)", 0)
    args.output_dir = _ask("Output folder", str(DIST_DIR))
    args.installer = _confirm("Create Inno Setup installer after the build?", False)
    return args


def _find_ico():
    for cand in (ROOT / "assets" / "icon.ico", ROOT / "server" / "icon.ico", ROOT / "icon.ico"):
        if cand.exists():
            return str(cand)
    return None


def _inside_round_rect(x, y, x0, y0, x1, y1, r):
    if x < x0 or x > x1 or y < y0 or y > y1:
        return False
    cx = min(max(x, x0 + r), x1 - r)
    cy = min(max(y, y0 + r), y1 - r)
    dx, dy = x - cx, y - cy
    return dx * dx + dy * dy <= r * r


def _render_png():
    size = 256
    bars = ((66, 90), (113, 137), (160, 184))
    pixels = bytearray(size * size * 4)
    for y in range(size):
        for x in range(size):
            r = g = b = a = 0
            for sy in (0.0, 0.5):
                for sx in (0.0, 0.5):
                    px = x + sx
                    py = y + sy
                    if not _inside_round_rect(px, py, 0, 0, 255, 255, 56):
                        continue
                    t = (px + py) / (2 * 255)
                    cr = int(0x26 + (0x6C - 0x26) * t)
                    cg = int(0x23 + (0x63 - 0x23) * t)
                    cb = int(0x35 + (0xFF - 0x35) * t)
                    ca = 255
                    for by0, by1 in bars:
                        if _inside_round_rect(px, py, 42, by0, 214, by1, 9):
                            cr = cg = cb = 255
                            break
                    r += cr
                    g += cg
                    b += cb
                    a += ca
            i = (y * size + x) * 4
            pixels[i] = r // 4
            pixels[i + 1] = g // 4
            pixels[i + 2] = b // 4
            pixels[i + 3] = a // 4
    return bytes(pixels)


def _build_png(raw_rgba, width, height):
    def chunk(typ, data):
        return (
            struct.pack(">I", len(data))
            + typ
            + data
            + struct.pack(">I", zlib.crc32(typ + data) & 0xFFFFFFFF)
        )

    signature = b"\x89PNG\r\n\x1a\n"
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
    stride = width * 4
    raw = b"".join(b"\x00" + raw_rgba[y * stride:(y + 1) * stride] for y in range(height))
    return (
        signature
        + chunk(b"IHDR", ihdr)
        + chunk(b"IDAT", zlib.compress(raw, 9))
        + chunk(b"IEND", b"")
    )


def _generate_ico():
    try:
        png = _build_png(_render_png(), 256, 256)
        header = struct.pack("<HHH", 0, 1, 1)
        entry = struct.pack("<BBBBHHII", 0, 0, 0, 0, 1, 32, len(png), 22)
        ico = header + entry + png
        out_dir = ROOT / ".build"
        out_dir.mkdir(exist_ok=True)
        ico_path = out_dir / f"{EXE_NAME}_icon.ico"
        ico_path.write_bytes(ico)
        return str(ico_path)
    except Exception as exc:
        print(f"Failed to generate icon: {exc}")
        return None


def _ensure_ico():
    existing = _find_ico()
    if existing:
        return existing
    generated = _generate_ico()
    if generated:
        print(f"Generated icon: {generated}")
        return generated
    print("Warning: icon not found, building without an icon.")
    return None


def _write_iss(ico_path, source_path, output_dir):
    iss = output_dir / "installer.iss"
    iss.parent.mkdir(parents=True, exist_ok=True)
    is_onedir = Path(source_path).is_dir()
    if is_onedir:
        source_line = (
            f'Source: "{source_path}\\*"; DestDir: "{{app}}"; '
            "Flags: ignoreversion recursesubdirs createallsubdirs"
        )
    else:
        source_line = f'Source: "{source_path}"; DestDir: "{{app}}"; Flags: ignoreversion'
    app_id = str(uuid.uuid5(uuid.NAMESPACE_DNS, f"com.uroboros.{EXE_NAME.lower()}"))
    lines = [
        "[Setup]",
        f"AppName={APP_NAME}",
        f"AppVersion={APP_VERSION}",
        f"AppVerName={APP_NAME} {APP_VERSION}",
        "AppPublisher=Uroboros",
        f"AppId={app_id}",
        f"DefaultDirName={{autopf}}\\{EXE_NAME}",
        f"DefaultGroupName={APP_NAME}",
        f"OutputDir={output_dir.as_posix()}",
        f"OutputBaseFilename={EXE_NAME}_Setup_v{APP_VERSION}_{PLATFORM_TAG}",
    ]
    if ico_path:
        lines.append(f"SetupIconFile={ico_path}")
    lines += [
        f"UninstallDisplayIcon={{app}}\\{EXE_NAME}.exe",
        "Compression=lzma2/max",
        "SolidCompression=yes",
        "ArchitecturesInstallIn64BitMode=x64compatible",
        "PrivilegesRequired=lowest",
        "DisableDirPage=auto",
        "DisableProgramGroupPage=auto",
        "UsePreviousAppDir=yes",
        "UsePreviousGroup=yes",
        "UpdateUninstallLogAppName=no",
        "",
        "[Tasks]",
        'Name: "desktopicon"; Description: "Create a &desktop shortcut"; GroupDescription: "Additional icons:"',
        'Name: "startmenu"; Description: "Create a &Start Menu shortcut"; GroupDescription: "Additional icons:"',
        "",
        "[Files]",
        source_line,
        "",
        "[Icons]",
        f'Name: "{{autoprograms}}\\{EXE_NAME}"; Filename: "{{app}}\\{EXE_NAME}.exe"; WorkingDir: "{{app}}"; Tasks: startmenu',
        f'Name: "{{autodesktop}}\\{EXE_NAME}"; Filename: "{{app}}\\{EXE_NAME}.exe"; WorkingDir: "{{app}}"; Tasks: desktopicon',
        "",
        "[Run]",
        f'Filename: "{{app}}\\{EXE_NAME}.exe"; Description: "Run {APP_NAME}"; Flags: postinstall nowait skipifsilent',
    ]
    iss.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return str(iss)


def _build_installer(ico_path, dist_path, output_dir):
    iscc = shutil.which("ISCC.exe") or shutil.which("iscc")
    if not iscc:
        candidates = [
            "C:/Program Files (x86)/Inno Setup 6/ISCC.exe",
            "C:/Program Files/Inno Setup 6/ISCC.exe",
            "C:/Program Files (x86)/Inno Setup 7/ISCC.exe",
            "C:/Program Files/Inno Setup 7/ISCC.exe",
        ]
        for cand in candidates:
            if Path(cand).exists():
                iscc = cand
                break
    if not iscc:
        iss = _write_iss(ico_path, dist_path, output_dir)
        print("\nInno Setup not found. Install it from: https://jrsoftware.org/isdl.php")
        print("Then rerun with --installer or compile the .iss file manually.")
        print(f"Installer script file: {iss}")
        return
    iss = _write_iss(ico_path, dist_path, output_dir)
    print(f"\nCompiling installer: {iscc}")
    result = subprocess.run([iscc, iss], cwd=str(ROOT))
    if result.returncode != 0:
        print(f"\nInstaller compilation failed (code {result.returncode})")
        sys.exit(result.returncode)
    installer = output_dir / f"{EXE_NAME}_Setup_v{APP_VERSION}_{PLATFORM_TAG}.exe"
    if installer.exists():
        size = installer.stat().st_size / 1024 / 1024
        print(f"\nInstaller created: {installer} ({size:.1f} MB)")
    else:
        print("\nInstaller path not found after compilation.")


def _find_dist_dir(output_dir):
    for child in output_dir.iterdir():
        if child.is_dir() and child.name.endswith(".dist"):
            return child
    return None


def _mb(size_bytes):
    return size_bytes / 1024 / 1024


def main():
    parser = argparse.ArgumentParser(description=f"{APP_NAME} Nuitka build")
    parser.add_argument("--onefile", action="store_true", help="Single EXE file (default is onedir)")
    parser.add_argument("--output-dir", default=str(DIST_DIR), help="Output folder")
    parser.add_argument("--clean", action="store_true", help="Clean the output folder before building")
    parser.add_argument("--jobs", type=int, default=0, help="Number of parallel jobs (0 = auto)")
    parser.add_argument("--installer", action="store_true", help="Create Inno Setup installer")
    known, remaining = parser.parse_known_args()

    if importlib.util.find_spec("nuitka") is None:
        print("Nuitka is not installed.")
        print("Install it first with: python -m pip install nuitka")
        sys.exit(1)

    if len(sys.argv) == 1:
        known = _interactive(known)

    OUTPUT_DIR = Path(known.output_dir)

    ico_path = _ensure_ico()

    if known.clean and OUTPUT_DIR.exists():
        shutil.rmtree(OUTPUT_DIR)
        print(f"Output folder cleaned: {OUTPUT_DIR}")

    cmd = [
        sys.executable,
        "-m",
        "nuitka",
        "--standalone",
        f"--output-dir={OUTPUT_DIR}",
        f"--output-filename={EXE_NAME}",
        "--include-package=server",
        "--include-data-dir=server/web/templates=server/web/templates",
        "--include-data-dir=server/web/static=server/web/static",
        "--warn-unusual-code",
        "--remove-output",
    ]
    if ico_path:
        if sys.platform == "win32":
            cmd.append(f"--windows-icon-from-ico={ico_path}")
        else:
            cmd.append(f"--linux-icon={ico_path}")
    if known.onefile:
        cmd.append("--onefile")
    if known.jobs:
        cmd.append(f"--jobs={known.jobs}")
    cmd.append(str(ROOT / "server" / "main.py"))

    mode = "onefile" if known.onefile else "onedir"
    print(f"\nBuilding {APP_NAME} v{APP_VERSION} ({mode}, {PLATFORM_TAG})")
    print(f"Python: {sys.executable}")
    print(f"Output folder: {OUTPUT_DIR}")
    print()

    result = subprocess.run(cmd, cwd=str(ROOT))

    if result.returncode != 0:
        print(f"\nBuild finished with an error (code {result.returncode})")
        sys.exit(result.returncode)

    if known.onefile:
        exe = OUTPUT_DIR / f"{EXE_NAME}.exe"
        if exe.exists():
            print(f"\nBuild successful! {exe} ({_mb(exe.stat().st_size):.1f} MB)")
        dist_path = str(exe)
    else:
        src_dir = _find_dist_dir(OUTPUT_DIR)
        dst_dir = OUTPUT_DIR / f"{EXE_NAME}.dist"
        if src_dir:
            if dst_dir.exists():
                shutil.rmtree(dst_dir)
            src_dir.rename(dst_dir)
        if dst_dir.exists():
            total = sum(f.stat().st_size for f in dst_dir.rglob("*") if f.is_file())
            print(f"\nBuild successful! {dst_dir} ({_mb(total):.1f} MB)")
        dist_path = str(dst_dir)

    if known.installer:
        _build_installer(ico_path, dist_path, OUTPUT_DIR)


if __name__ == "__main__":
    main()
