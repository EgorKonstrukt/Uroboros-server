import asyncio
import re
import shutil
import subprocess
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

import aiohttp

from server.mc.config import default_server_dir
from server.mc.download import DownloadHandle

USER_AGENT = "Uroboros/1.0"

VANILLA_MANIFEST_URLS = [
    "https://piston-meta.mojang.com/mc/game/version_manifest_v2.json",
    "https://launchermeta.mojang.com/mc/game/version_manifest_v2.json",
]
PAPER_API_URLS = [
    "https://fill.papermc.io/v3",
]
PURPUR_API = "https://api.purpurmc.org/v2"
FABRIC_META = "https://meta.fabricmc.net/v2"
QUILT_META = "https://meta.quiltmc.org/v3"
FORGE_MAVEN = "https://maven.minecraftforge.net/net/minecraftforge/forge"
FORGE_PROMOTIONS = "https://files.minecraftforge.net/net/minecraftforge/forge/promotions_slim.json"
NEOFORGE_MAVEN = "https://maven.neoforged.net/releases/net/neoforged/neoforge"
SPIGOT_BUILDTOOLS_URL = "https://hub.spigotmc.org/jenkins/job/BuildTools/lastSuccessfulBuild/artifact/target/BuildTools.jar"
ARCLIGHT_API = "https://files.hypoglycemia.icu/v1/files/arclight/minecraft"

CORE_TYPES = [
    {
        "id": "vanilla",
        "label": "Vanilla",
        "description": "Official Mojang Minecraft server",
        "has_builds": False,
        "loader_versions": False,
        "installer": False,
    },
    {
        "id": "paper",
        "label": "Paper",
        "description": "High performance fork of Spigot with modern optimizations",
        "has_builds": True,
        "loader_versions": False,
        "installer": False,
    },
    {
        "id": "purpur",
        "label": "Purpur",
        "description": "Fork of Paper with extra configurability",
        "has_builds": True,
        "loader_versions": False,
        "installer": False,
    },
    {
        "id": "spigot",
        "label": "Spigot",
        "description": "Most widely used server software (built with BuildTools)",
        "has_builds": False,
        "loader_versions": False,
        "installer": True,
    },
    {
        "id": "bukkit",
        "label": "Bukkit (CraftBukkit)",
        "description": "Original Bukkit server (built with BuildTools)",
        "has_builds": False,
        "loader_versions": False,
        "installer": True,
    },
    {
        "id": "fabric",
        "label": "Fabric",
        "description": "Lightweight and modular mod loader",
        "has_builds": False,
        "loader_versions": True,
        "installer": False,
    },
    {
        "id": "quilt",
        "label": "Quilt",
        "description": "Modern fork of Fabric with an enhanced toolchain",
        "has_builds": False,
        "loader_versions": True,
        "installer": False,
    },
    {
        "id": "forge",
        "label": "Forge",
        "description": "Classic mod loader for Minecraft",
        "has_builds": False,
        "loader_versions": True,
        "installer": True,
    },
    {
        "id": "neoforge",
        "label": "NeoForge",
        "description": "Community driven fork of Forge",
        "has_builds": False,
        "loader_versions": True,
        "installer": True,
    },
    {
        "id": "arclight",
        "label": "Arclight",
        "description": "A Bukkit server implementation on common mod loaders (Forge/NeoForge/Fabric)",
        "has_builds": False,
        "loader_versions": True,
        "installer": False,
    },
]


class CoreError(Exception):
    pass


def get_core_types() -> list:
    return [dict(t) for t in CORE_TYPES]


def _get_core_type(core_id: str) -> dict:
    for t in CORE_TYPES:
        if t["id"] == core_id:
            return t
    raise CoreError(f"Unknown core type: {core_id}")


def _version_key(value: str):
    nums = re.findall(r"\d+", value or "")
    return tuple(int(n) for n in nums)


async def _http_get_json(url, timeout=60):
    try:
        async with aiohttp.ClientSession(headers={"User-Agent": USER_AGENT}) as session:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=timeout)) as resp:
                if resp.status != 200:
                    raise CoreError(f"HTTP {resp.status} from {url}")
                try:
                    return await resp.json(content_type=None)
                except Exception:
                    raise CoreError(f"Invalid JSON response from {url}")
    except (aiohttp.ClientError, OSError) as e:
        raise CoreError(f"Failed to connect to {url}: {e}")


async def _http_get_xml(url, timeout=60):
    try:
        async with aiohttp.ClientSession(headers={"User-Agent": USER_AGENT}) as session:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=timeout)) as resp:
                if resp.status != 200:
                    raise CoreError(f"HTTP {resp.status} from {url}")
                text = await resp.text()
                try:
                    return ET.fromstring(text)
                except ET.ParseError:
                    raise CoreError(f"Invalid XML response from {url}")
    except (aiohttp.ClientError, OSError) as e:
        raise CoreError(f"Failed to connect to {url}: {e}")


async def _http_get_json_first(urls, timeout=60):
    last = None
    for url in urls:
        try:
            return await _http_get_json(url, timeout=timeout)
        except CoreError as e:
            last = e
    raise last or CoreError("No endpoints available")


async def _http_download(url, dest: Path, handle=None):
    if handle is None:
        handle = DownloadHandle()
    await handle.download(url, dest)


def _server_dir(instance) -> Path:
    raw = getattr(instance, "server_dir", "") or ""
    path = Path(raw) if raw else Path(default_server_dir(instance.id))
    path.mkdir(parents=True, exist_ok=True)
    return path


def _java_for(instance) -> str:
    return getattr(instance, "java_executable_path", "") or "java"


def _make_result(core_id, version, core_version, path: Path) -> dict:
    return {
        "core": core_id,
        "version": version,
        "core_version": core_version,
        "server_filename": path.name,
        "path": str(path),
        "size": path.stat().st_size if path.exists() else 0,
    }


def _run_installer(java, jar, cwd, *args, timeout=1800):
    cmd = [java, "-jar", str(jar), *args]
    result = subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True, timeout=timeout)
    if result.returncode != 0:
        tail = ((result.stdout or "") + (result.stderr or ""))[-3000:]
        raise CoreError(f"Installer failed: {tail}")


async def _vanilla_manifest():
    return await _http_get_json_first(VANILLA_MANIFEST_URLS)


async def _vanilla_versions():
    data = await _vanilla_manifest()
    out = []
    for v in data.get("versions", []):
        if v.get("type") not in ("release", "snapshot"):
            continue
        out.append({
            "version": v["id"],
            "label": v["id"],
            "type": v.get("type", ""),
            "release": v.get("type") == "release",
        })
    out.sort(key=lambda x: _version_key(x["version"]), reverse=True)
    return out


async def _paper_versions():
    data = await _http_get_json_first([u + "/projects/paper" for u in PAPER_API_URLS])
    raw = data.get("versions", [])
    flat = []
    if isinstance(raw, dict):
        for group in raw.values():
            flat.extend(group if isinstance(group, list) else [group])
    else:
        flat = list(raw)
    out = [{"version": v, "label": v, "type": "release", "release": True} for v in flat]
    out.sort(key=lambda x: _version_key(x["version"]), reverse=True)
    return out


async def _paper_builds(version):
    data = await _http_get_json_first([u + f"/projects/paper/versions/{version}/builds" for u in PAPER_API_URLS])
    builds = data if isinstance(data, list) else data.get("builds", [])
    out = []
    for b in builds:
        bid = b.get("id", b.get("build"))
        if bid is None:
            continue
        out.append({
            "id": str(bid),
            "label": f"Build {bid} ({b.get('channel', '')})",
            "recommended": b.get("channel") in ("STABLE", "RECOMMENDED"),
        })
    out.sort(key=lambda x: int(x["id"]), reverse=True)
    return out


async def _purpur_versions():
    data = await _http_get_json(PURPUR_API + "/purpur")
    out = [{"version": v, "label": v, "type": "release", "release": True} for v in data.get("versions", [])]
    out.sort(key=lambda x: _version_key(x["version"]), reverse=True)
    return out


async def _purpur_builds(version):
    data = await _http_get_json(f"{PURPUR_API}/purpur/{version}")
    builds = data.get("builds", {})
    latest = str(builds.get("latest", ""))
    out = []
    for b in builds.get("all", []):
        bstr = str(b)
        out.append({"id": bstr, "label": f"Build {bstr}", "recommended": bstr == latest})
    return out


async def _release_versions():
    data = await _vanilla_manifest()
    out = []
    for v in data.get("versions", []):
        if v.get("type") == "release":
            out.append({"version": v["id"], "label": v["id"], "type": "release", "release": True})
    return out


async def _fabric_versions():
    data = await _http_get_json(FABRIC_META + "/versions/game")
    out = []
    for v in data:
        vid = v.get("version", "")
        if not vid:
            continue
        out.append({
            "version": vid,
            "label": vid,
            "type": "release" if v.get("stable") else "snapshot",
            "release": bool(v.get("stable")),
        })
    out.sort(key=lambda x: _version_key(x["version"]), reverse=True)
    return out


async def _fabric_loaders(version):
    data = await _http_get_json(f"{FABRIC_META}/versions/loader/{version}")
    out = []
    for v in data:
        loader = v.get("loader") or {}
        vid = loader.get("version", "")
        if not vid:
            continue
        out.append({"id": vid, "label": vid, "recommended": bool(loader.get("stable"))})
    return out


async def _fabric_latest_installer():
    data = await _http_get_json(FABRIC_META + "/versions/installer")
    return data[0]["version"] if data else "0.15.11"


async def _quilt_versions():
    data = await _http_get_json(QUILT_META + "/versions/game")
    out = []
    for v in data:
        vid = v.get("version", "")
        if not vid:
            continue
        out.append({
            "version": vid,
            "label": vid,
            "type": "release" if v.get("stable") else "snapshot",
            "release": bool(v.get("stable")),
        })
    out.sort(key=lambda x: _version_key(x["version"]), reverse=True)
    return out


async def _quilt_loaders(version):
    data = await _http_get_json(f"{QUILT_META}/versions/loader/{version}")
    out = []
    for v in data:
        loader = v.get("loader") or {}
        vid = loader.get("version", "")
        if not vid:
            continue
        out.append({"id": vid, "label": vid, "recommended": bool(loader.get("stable"))})
    return out


async def _quilt_latest_installer():
    data = await _http_get_json(QUILT_META + "/versions/installer")
    return data[0]["version"] if data else "0.6.0"


async def _maven_versions(meta_url):
    meta = await _http_get_xml(meta_url)
    return [v.text for v in meta.findall(".//version") if v.text]


async def _forge_versions():
    versions = await _maven_versions(FORGE_MAVEN + "/maven-metadata.xml")
    seen = set()
    out = []
    for ver in versions:
        mc = ver.split("-", 1)[0] if "-" in ver else ""
        if mc and mc not in seen:
            seen.add(mc)
            out.append({"version": mc, "label": mc, "type": "release", "release": True})
    out.sort(key=lambda x: _version_key(x["version"]), reverse=True)
    return out


async def _forge_loaders(version):
    versions = await _maven_versions(FORGE_MAVEN + "/maven-metadata.xml")
    promos = {}
    try:
        promos = (await _http_get_json(FORGE_PROMOTIONS)).get("promos", {})
    except CoreError:
        pass
    recommended = promos.get(f"{version}-recommended")
    latest = promos.get(f"{version}-latest")
    out = []
    for ver in versions:
        if ver.startswith(version + "-"):
            loader = ver.split("-", 1)[1]
            out.append({
                "id": loader,
                "label": loader,
                "recommended": loader == recommended,
                "latest": loader == latest,
            })
    return out


def _neoforge_prefix(mc_version: str) -> str:
    parts = mc_version.split(".")
    if parts[0] == "1":
        rest = parts[1:]
        if len(rest) == 1:
            return rest[0] + ".0"
        return ".".join(rest)
    return mc_version


def _mc_from_neoforge(nf_version: str) -> str:
    base = nf_version.split("-", 1)[0]
    parts = base.split(".")
    if not parts:
        return base
    if parts[0] == "1":
        return ".".join(parts[:2])
    if parts[0] in ("20", "21"):
        if len(parts) > 1 and parts[1] != "0":
            return f"1.{parts[0]}.{parts[1]}"
        return f"1.{parts[0]}"
    return ".".join(parts[:2])


async def _neoforge_versions():
    versions = await _maven_versions(NEOFORGE_MAVEN + "/maven-metadata.xml")
    seen = set()
    out = []
    for ver in versions:
        mc = _mc_from_neoforge(ver)
        if not mc or mc in seen:
            continue
        seen.add(mc)
        out.append({"version": mc, "label": mc, "type": "release", "release": True})
    out.sort(key=lambda x: _version_key(x["version"]), reverse=True)
    return out


async def _neoforge_loaders(version):
    prefix = _neoforge_prefix(version)
    versions = await _maven_versions(NEOFORGE_MAVEN + "/maven-metadata.xml")
    out = []
    for ver in versions:
        base = ver.split("-", 1)[0]
        if not base.startswith(prefix + "."):
            continue
        out.append({"id": ver, "label": ver, "recommended": "-" not in ver, "latest": False})
    out.sort(key=lambda x: _version_key(x["id"]), reverse=True)
    out.sort(key=lambda x: "-" in x["id"])
    return out


async def _arclight_versions():
    data = await _http_get_json(ARCLIGHT_API)
    out = []
    for f in data.get("files", []):
        name = f.get("name", "")
        if not name:
            continue
        out.append({"version": name, "label": name, "type": "release", "release": True})
    out.sort(key=lambda x: _version_key(x["version"]), reverse=True)
    return out


async def _arclight_loaders(version):
    data = await _http_get_json(f"{ARCLIGHT_API}/{version}/loaders")
    out = []
    for f in data.get("files", []):
        name = f.get("name", "")
        if not name:
            continue
        out.append({"id": name, "label": name, "recommended": False})
    return out


async def _arclight_versions_for_loader(version, loader):
    data = await _http_get_json(f"{ARCLIGHT_API}/{version}/loaders/{loader}/versions-snapshot")
    out = []
    for f in data.get("files", []):
        name = f.get("name", "")
        if not name:
            continue
        stable = "-SNAPSHOT" not in name
        out.append({"id": name, "label": name if stable else f"{name} (snapshot)", "recommended": stable})
    return out


async def get_core_versions(core_id: str) -> list:
    _get_core_type(core_id)
    if core_id == "vanilla":
        return await _vanilla_versions()
    if core_id == "paper":
        return await _paper_versions()
    if core_id == "purpur":
        return await _purpur_versions()
    if core_id in ("spigot", "bukkit"):
        return await _release_versions()
    if core_id == "fabric":
        return await _fabric_versions()
    if core_id == "quilt":
        return await _quilt_versions()
    if core_id == "forge":
        return await _forge_versions()
    if core_id == "neoforge":
        return await _neoforge_versions()
    if core_id == "arclight":
        return await _arclight_versions()
    raise CoreError(f"Unknown core type: {core_id}")


async def get_core_builds(core_id: str, version: str, loader: str = "") -> list:
    _get_core_type(core_id)
    if core_id == "paper":
        return await _paper_builds(version)
    if core_id == "purpur":
        return await _purpur_builds(version)
    if core_id == "fabric":
        return await _fabric_loaders(version)
    if core_id == "quilt":
        return await _quilt_loaders(version)
    if core_id == "forge":
        return await _forge_loaders(version)
    if core_id == "neoforge":
        return await _neoforge_loaders(version)
    if core_id == "arclight":
        if loader:
            return await _arclight_versions_for_loader(version, loader)
        return await _arclight_loaders(version)
    return []


async def _install_vanilla(instance, version, filename, handle):
    data = await _vanilla_manifest()
    target = None
    for v in data.get("versions", []):
        if v["id"] == version:
            target = v
            break
    if target is None:
        raise CoreError(f"Version {version} not found in Minecraft manifest")
    vdata = await _http_get_json(target["url"])
    server = vdata.get("downloads", {}).get("server", {})
    if not server or not server.get("url"):
        raise CoreError(f"Version {version} has no server download")
    fname = filename or "server.jar"
    dest = _server_dir(instance) / fname
    await _http_download(server["url"], dest, handle)
    return _make_result("vanilla", version, version, dest)


async def _install_paper(instance, version, build, filename, handle):
    if not build:
        builds = await _paper_builds(version)
        if not builds:
            raise CoreError("No builds available for this version")
        stable = [b for b in builds if b["recommended"]]
        build = (stable or builds)[0]["id"]
    data = await _http_get_json_first([u + f"/projects/paper/versions/{version}/builds" for u in PAPER_API_URLS])
    builds = data if isinstance(data, list) else data.get("builds", [])
    bobj = None
    for b in builds:
        if str(b.get("id", b.get("build"))) == str(build):
            bobj = b
            break
    if bobj is None:
        raise CoreError(f"Build {build} not found for version {version}")
    downloads = bobj.get("downloads") or {}
    dl = downloads.get("server:default") or downloads.get("server") or {}
    if not dl or not dl.get("url"):
        raise CoreError("No server download available for this build")
    url = dl["url"]
    fname = filename or dl.get("name") or f"paper-{version}-{build}.jar"
    dest = _server_dir(instance) / fname
    await _http_download(url, dest, handle)
    return _make_result("paper", version, f"build {build}", dest)


async def _install_purpur(instance, version, build, filename, handle):
    if not build:
        data = await _http_get_json(f"{PURPUR_API}/purpur/{version}")
        build = str(data.get("builds", {}).get("latest", ""))
    if not build:
        raise CoreError("No builds available for this version")
    url = f"{PURPUR_API}/purpur/{version}/{build}/download"
    fname = filename or f"purpur-{version}-{build}.jar"
    dest = _server_dir(instance) / fname
    await _http_download(url, dest, handle)
    return _make_result("purpur", version, f"build {build}", dest)


async def _install_fabric(instance, version, loader_version, filename, handle):
    if not loader_version:
        loaders = await _fabric_loaders(version)
        if not loaders:
            raise CoreError("No loader versions available for this version")
        loader_version = loaders[0]["id"]
    installer = await _fabric_latest_installer()
    url = f"{FABRIC_META}/versions/loader/{version}/{loader_version}/{installer}/server/jar"
    fname = filename or f"fabric-server-mc.{version}.loader.{loader_version}.launcher.jar"
    dest = _server_dir(instance) / fname
    await _http_download(url, dest, handle)
    return _make_result("fabric", version, f"loader {loader_version}", dest)


async def _install_quilt(instance, version, loader_version, filename, handle):
    if not loader_version:
        loaders = await _quilt_loaders(version)
        if not loaders:
            raise CoreError("No loader versions available for this version")
        loader_version = loaders[0]["id"]
    installer = await _quilt_latest_installer()
    installer_url = f"https://maven.quiltmc.org/repository/release/org/quiltmc/quilt-installer/{installer}/quilt-installer-{installer}.jar"
    server_dir = _server_dir(instance)
    with tempfile.TemporaryDirectory() as td:
        qi = Path(td) / "quilt-installer.jar"
        await _http_download(installer_url, qi, handle)
        if handle:
            handle.update(status="installing", current=0, total=0, message="Running Quilt installer...")
        java = _java_for(instance)
        cmd = [java, "-jar", str(qi), "install", "server", version, loader_version, "--download-server"]
        result = await asyncio.to_thread(
            subprocess.run, cmd, cwd=str(server_dir), capture_output=True, text=True, timeout=1800,
        )
        if result.returncode != 0:
            tail = ((result.stdout or "") + (result.stderr or ""))[-3000:]
            raise CoreError(f"Quilt installer failed: {tail}")
    jar = server_dir / "quilt-server-launch.jar"
    if not jar.exists():
        raise CoreError("Quilt installer did not produce a server launch jar")
    if filename and jar.name != filename:
        final = server_dir / filename
        shutil.move(str(jar), str(final))
        jar = final
    return _make_result("quilt", version, f"loader {loader_version}", jar)


async def _install_forge(instance, version, loader_version, filename, handle):
    if not loader_version:
        loaders = await _forge_loaders(version)
        if not loaders:
            raise CoreError("No loader versions available for this version")
        loader_version = loaders[0]["id"]
    full = f"{version}-{loader_version}"
    installer_url = f"{FORGE_MAVEN}/{full}/forge-{full}-installer.jar"
    server_dir = _server_dir(instance)
    with tempfile.TemporaryDirectory() as td:
        installer = Path(td) / "forge-installer.jar"
        await _http_download(installer_url, installer, handle)
        if handle:
            handle.update(status="installing", current=0, total=0, message="Running Forge installer...")
        java = _java_for(instance)
        await asyncio.to_thread(_run_installer, java, installer, server_dir, "--installServer")
    jar = server_dir / f"forge-{full}.jar"
    if not jar.exists():
        shim = server_dir / f"forge-{full}-shim.jar"
        if shim.exists():
            jar = shim
    if not jar.exists():
        raise CoreError("Forge installer did not produce a server jar")
    if filename and jar.name != filename:
        final = server_dir / filename
        shutil.move(str(jar), str(final))
        jar = final
    return _make_result("forge", version, full, jar)


async def _install_neoforge(instance, version, loader_version, filename, handle):
    if not loader_version:
        loaders = await _neoforge_loaders(version)
        if not loaders:
            raise CoreError("No loader versions available for this version")
        loader_version = loaders[0]["id"]
    installer_url = f"{NEOFORGE_MAVEN}/{loader_version}/neoforge-{loader_version}-installer.jar"
    server_dir = _server_dir(instance)
    with tempfile.TemporaryDirectory() as td:
        installer = Path(td) / "neoforge-installer.jar"
        await _http_download(installer_url, installer, handle)
        if handle:
            handle.update(status="installing", current=0, total=0, message="Running NeoForge installer...")
        java = _java_for(instance)
        await asyncio.to_thread(_run_installer, java, installer, server_dir, "--installServer")
    candidates = [
        server_dir / f"neoforge-{loader_version}.jar",
        server_dir / "libraries" / "net" / "neoforged" / "neoforge" / loader_version / f"neoforge-{loader_version}-server.jar",
    ]
    jar = next((c for c in candidates if c.exists()), None)
    if jar is None:
        found = list(server_dir.glob("libraries/**/neoforge-*-server.jar"))
        if found:
            jar = sorted(found, key=lambda p: p.stat().st_mtime, reverse=True)[0]
    if jar is None:
        raise CoreError("NeoForge installer did not produce a server jar")
    if filename and jar.name != filename:
        final = server_dir / filename
        shutil.copy2(str(jar), str(final))
        jar = final
    if filename:
        rel = filename
    else:
        rel = str(jar.relative_to(server_dir)).replace("\\", "/")
    return _make_result("neoforge", version, loader_version, jar) | {"server_filename": rel}


async def _install_arclight(instance, version, loader, arclight_version, filename, handle):
    if not loader:
        raise CoreError("No loader selected (fabric, forge or neoforge)")
    if not arclight_version:
        builds = await _arclight_versions_for_loader(version, loader)
        if not builds:
            raise CoreError("No Arclight builds available for this version/loader")
        stable = [b for b in builds if b["recommended"]]
        arclight_version = (stable or builds)[0]["id"]
    data = await _http_get_json(f"{ARCLIGHT_API}/{version}/loaders/{loader}/versions-snapshot")
    target = None
    for f in data.get("files", []):
        if f.get("name") == arclight_version:
            target = f
            break
    if target is None:
        raise CoreError(f"Arclight build {arclight_version} not found for MC {version} ({loader})")
    url = target.get("permlink") or target.get("link") or ""
    if not url:
        raise CoreError("No download link available for this Arclight build")
    fname = filename or f"arclight-{loader}-{version}-{arclight_version}.jar"
    dest = _server_dir(instance) / fname
    await _http_download(url, dest, handle)
    return _make_result("arclight", version, f"{loader} {arclight_version}", dest)


def _build_buildtools(server_dir, java, version, target, tmp, filename):
    bt = tmp / "BuildTools.jar"
    cmd = [java, "-jar", str(bt), "--rev", version]
    if target == "bukkit":
        cmd.extend(["--compile", "craftbukkit"])
    result = subprocess.run(cmd, cwd=str(tmp), capture_output=True, text=True, timeout=3600)
    if result.returncode != 0:
        tail = ((result.stdout or "") + (result.stderr or ""))[-3000:]
        raise CoreError(f"BuildTools failed: {tail}")
    if target == "spigot":
        candidates = [
            tmp / f"spigot-{version}.jar",
            tmp / f"spigot-{version}-SNAPSHOT.jar",
        ]
        found = list(tmp.glob("spigot-*.jar"))
    else:
        candidates = [
            tmp / f"craftbukkit-{version}.jar",
            tmp / f"craftbukkit-{version}-SNAPSHOT.jar",
        ]
        found = list(tmp.glob("craftbukkit-*.jar"))
    jar = next((c for c in candidates if c.exists()), None)
    if jar is None and found:
        jar = found[0]
    if jar is None:
        raise CoreError("BuildTools finished but produced no server jar")
    dest = server_dir / (filename or jar.name)
    shutil.copy2(str(jar), str(dest))
    return dest


async def _install_spigot(instance, version, filename, handle, target):
    server_dir = _server_dir(instance)
    java = _java_for(instance)
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        bt = tmp / "BuildTools.jar"
        if handle:
            handle.update(status="downloading", current=0, total=0, message="Downloading BuildTools...")
        await _http_download(SPIGOT_BUILDTOOLS_URL, bt, handle)
        if handle:
            handle.update(status="installing", current=0, total=0, message=f"Building {target} via BuildTools (this can take a long time)...")
        dest = await asyncio.to_thread(_build_buildtools, server_dir, java, version, target, tmp, filename)
    return _make_result("spigot" if target == "spigot" else "bukkit", version, version, dest)


async def install_server_core(instance, core_id, version, build=None, loader_version=None, filename=None, handle=None) -> dict:
    _get_core_type(core_id)
    if core_id == "vanilla":
        return await _install_vanilla(instance, version, filename, handle)
    if core_id == "paper":
        return await _install_paper(instance, version, build, filename, handle)
    if core_id == "purpur":
        return await _install_purpur(instance, version, build, filename, handle)
    if core_id == "spigot":
        return await _install_spigot(instance, version, filename, handle, "spigot")
    if core_id == "bukkit":
        return await _install_spigot(instance, version, filename, handle, "bukkit")
    if core_id == "fabric":
        return await _install_fabric(instance, version, loader_version or build, filename, handle)
    if core_id == "quilt":
        return await _install_quilt(instance, version, loader_version or build, filename, handle)
    if core_id == "forge":
        return await _install_forge(instance, version, loader_version or build, filename, handle)
    if core_id == "neoforge":
        return await _install_neoforge(instance, version, loader_version or build, filename, handle)
    if core_id == "arclight":
        return await _install_arclight(instance, version, loader_version or build, build, filename, handle)
    raise CoreError(f"Unknown core type: {core_id}")
