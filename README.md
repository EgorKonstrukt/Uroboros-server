# Uroboros Server

Uroboros Server is a self-hosted Minecraft server management platform with a built-in authentication server. It runs Minecraft servers on your own machine, exposes a compatible Yggdrasil API so launchers can authenticate against it, and gives you a browser-based admin panel to control everything.

The idea is simple: one process manages your account database, your web panel, and your game servers at the same time. There is no separate control plane, no external auth dependency, and no cloud account required.

## What it does

- Serves as an `authlib-injector` compatible Yggdrasil authentication server. Any modern launcher that supports third-party Yggdrasil can register accounts, log in, manage skins and join your servers.
- Runs one or more Minecraft server instances. Each instance is a normal server directory with its own JAR, Java runtime, JVM flags, memory limits and EULA handling.
- Manages modpacks for a launcher audience: projects, news, per-modpack metadata and file hosting with SHA-256 checksums.
- Provides a web admin panel for the whole stack: server control, console, file manager, player management, Java installation and self-updates.

## Features

**Server management**

- Multiple instances, each with its own working directory, Java binary, heap size, JVM flags and server arguments.
- Start, stop, restart and reload through the panel or the CLI.
- Live console output with tailing and cursor-based polling; no terminal multiplexer required.
- Crash detection with optional automatic restart, and graceful shutdown that sends `stop` before falling back to TERM/KILL.
- PID file tracking so instances can be stopped even if the panel process was restarted.
- `online-mode=true` is enforced and `authlib-injector` is downloaded automatically when auth is enabled.
- Optional whitelist mode that writes `whitelist.json` from the account database and reloads it.
- Optional EULA auto-accept.

**Authentication (Yggdrasil)**

- Full account lifecycle: register, authenticate, refresh, validate, invalidate, sign out.
- `scrypt` password hashing; session tokens are stored as SHA-256 hashes, never in plaintext.
- Login with username, display name or email (`feature.non_email_login` is enabled).
- Skins: upload PNG/JPEG via the panel or the auth API, classic or slim model. Textures are served as signed-style `textures` properties, as Mojang does.
- Rate limiting on auth and panel login endpoints, IP history per account, and optional `X-Forwarded-For` trust for reverse proxy deployments.
- Both the non-standard JSON endpoints (`/auth/*`) and the standard authlib-injector protocol paths (`/authserver/*`, `/sessionserver/*`) are served.

**Player management**

- Per-user records: UUID, login, display name, email, last IP and IP history.
- Ban and unban with a reason, optional duration, and scope (global or per-instance). Bans match accounts, nicknames, emails and IPs, and are written into each server's `banned-players.json`.
- Nickname and password changes propagate to whitelists and ban files automatically.
- Online player detection by probing running instances over the Minecraft protocol.

**Server cores**

Installs any of the common server software directly from official sources:

- Vanilla
- Paper and Folia
- Purpur
- Spigot and CraftBukkit (built on the fly via BuildTools)
- Fabric and Quilt
- Forge and NeoForge
- Arclight (Bukkit on a mod loader)

Core installs run as background tasks with progress reporting, so large downloads and BuildTools/Forge installer steps do not block the panel.

**Java management**

- Scans the system for installed JVMs: `PATH`, `JAVA_HOME`, standard install directories and the Windows registry.
- Downloads and installs managed runtimes from Adoptium (Temurin), Azul Zulu, Microsoft OpenJDK and Amazon Corretto.
- Tracks version, vendor and architecture per runtime.

**Modpacks**

- Projects with branding (logo, colors, window title) and news entries.
- Modpacks with metadata: Minecraft version, loader, loader version, memory hints, changelog.
- Import from CurseForge and Modrinth archives. Overrides are copied verbatim, and mods are resolved and downloaded from the respective APIs. CurseForge imports need an API key, which is configurable.
- SHA-256 hash index maintained per modpack for launcher-side file verification.
- A launcher-facing API for server lists, modpack manifests and file downloads, including a hosted copy of `authlib-injector.jar`.

**File manager**

Per instance and per modpack: browse, upload (single and batch), download (files and zipped directories), edit text files, create folders, rename, move, copy, delete, and create ZIP archives. Path traversal is checked on every operation.

**Panel and monitoring**

- No external frontend dependencies. Charts, layout and interaction are plain HTML/CSS/JS with a small custom chart renderer.
- Per-instance overview: process stats (CPU, RSS/VMS, threads, uptime, open files, connections) plus host CPU, memory and disk usage.
- Player counts are parsed from console output and from real status pings.
- TPS reporting for Paper-family and Forge-family servers, polled on an interval.

**Self-update**

- Checks GitHub for new releases, downloads the source archive, backs up the current tree to `.backups/`, and swaps files in place.
- Can stop running servers before updating and restart them afterwards, with an option to reinstall requirements.

## Requirements

- Python 3.13 or newer (3.10+ should work for most features, but 3.13 is the tested target).
- Java 8/17/21 depending on the Minecraft versions you run. Uroboros can install a managed runtime if the system Java is not suitable.
- Outbound HTTPS access to the Mojang/Paper/Purpur/Fabric/Quilt/Forge/NeoForge APIs for core installs, and to GitHub for updates.

Works on Windows and Linux. The Windows and Linux launch scripts handle most of the bootstrap for you.

## Quick start

The simplest path is the bundled launcher script:

```bat
run_server.bat
```

```sh
./run_server.sh
```

On first run the script locates or installs Python, creates a `.venv`, installs `requirements.txt`, and shows a menu with options for running the panel, starting/stopping servers, checking status, updating, building a compiled binary and toggling autostart.

To skip the menu and go straight to the panel:

```sh
run_server.bat --autostart
```

To run manually:

```sh
python -m venv .venv
# Windows: .venv\Scripts\activate | Linux/macOS: source .venv/bin/activate
pip install -r requirements.txt
python -m server run
```

On first start Uroboros generates an admin password for the panel and prints it to the console:

```
[Uroboros] Generated admin panel password: <random>
```

You can set your own at any time:

```sh
python -m server set-admin-password
python -m server set-admin-password 'your password here'
```

Open the panel at `http://<host>:25581/admin/`.

## Configuration

Configuration lives in `~/.uroboros/server/config.json` and is created on first run. The key options:

| Key | Default | Meaning |
| --- | --- | --- |
| `host` | `0.0.0.0` | Bind address for the HTTP server |
| `port` | `25581` | HTTP server port |
| `db_path` | `~/.uroboros/server/auth.db` | SQLite database location |
| `admin_password` | generated | scrypt hash of the panel password |
| `log_level` | `info` | Uvicorn log level |
| `curseforge_api_key` | empty | API key for CurseForge modpack imports |
| `ssl_certfile` / `ssl_keyfile` | empty | Enables HTTPS when both are set |
| `stats_refresh_seconds` | `2` | Overview auto-refresh interval (1–60) |
| `console_refresh_ms` | `500` | Console auto-refresh interval (100–60000) |
| `trust_proxy_headers` | `false` | Honor `X-Forwarded-For` from a trusted reverse proxy |

The panel can read and write all of these from **Config** in the admin UI. Changing `host`, `port` or `ssl_*` requires a restart.

## Command line

```
usage: python -m server <command> [options]

run        Run the HTTP server (auth + API + admin panel)
           --host, --port, --log-level

start      Start enabled Minecraft server instance(s)
           [instance_id]

stop       Stop Minecraft server instance(s)
           [instance_id]

status     Show running state, PID and live player/latency info
           [instance_id]

projects   List configured projects

version    Print the installed version

set-admin-password   Set the admin panel password (interactive if omitted)

update     Check for and apply updates from GitHub
           --check  only check
           --force  apply even if already on the latest version
           --yes    skip the confirmation prompt
```

`start`, `stop` and `status` without an instance ID operate on all instances.

## Admin panel

The panel is served under `/admin/` and protected by the admin password. Sections:

- **Overview** – per-instance health: running state, player count, TPS, CPU, memory, and a small live chart of the console.
- **Servers** – create and edit instances, install server cores, send console commands, start/stop/restart/reload, manage files, toggle whitelist mode.
- **Players** – registered accounts, online status, skins, IP history, bans.
- **Projects** – launcher-facing projects, news, modpacks and modpack file management.
- **Java** – detected and managed runtimes, plus in-panel installation.
- **Update** – check for a new release, apply it, and inspect previous backups.

The login endpoint is rate limited and issues a bearer token with a 24-hour lifetime.

## Launcher integration

Point the launcher's Yggdrasil API URL at your server root, e.g. `http://your-host:25581`. The root path advertises the metadata authlib-injector expects:

```json
{
  "meta": {
    "serverName": "Uroboros Server",
    "implementationName": "Uroboros Yggdrasil",
    "feature.non_email_login": true,
    "feature.no_mojang_namespace": true
  },
  "skinDomains": ["localhost", "127.0.0.1", "<your-host>"]
}
```

The launcher API:

- `GET /launcher/projects/{project}/servers` – enabled instances with live status, advertised address and linked modpack.
- `GET /launcher/sync/{project}` – project branding, modpacks and latest news.
- `GET /launcher/projects/{project}/modpacks/{modpack}/files` – file list with SHA-256 hashes.
- `GET /launcher/projects/{project}/modpacks/{modpack}/download/{path}` – file download.
- `GET /launcher/injector` – a hosted copy of the authlib-injector JAR.
- `GET /launcher/bans/{uuid}` – ban status for a player.

Yggdrasil protocol endpoints under `/authserver/`, `/sessionserver/` and `/api/` are served as authlib-injector expects, including the Mojang profile lookup `POST /api/profiles/minecraft`.

## Project layout

```
server/
├── app.py                  FastAPI application and routing
├── main.py                 CLI entry point
├── config.py               ServerConfig, persisted to ~/.uroboros/server/config.json
├── database.py             async SQLAlchemy engine and schema migration
├── models.py               SQLAlchemy models (users, sessions, bans, projects, modpacks, instances)
├── version.py              APP_NAME / APP_VERSION
├── auth/
│   ├── routes.py           /auth endpoints and Yggdrasil protocol wrappers
│   ├── crypto.py           scrypt hashing, token hashing, UUID/token generation
│   ├── schemas.py          Pydantic request/response models
│   └── ratelimit.py        sliding-window rate limiters
├── mc/
│   ├── core.py             server core installers (Vanilla/Paper/Folia/Purpur/Spigot/.../Arclight)
│   ├── manager.py          process lifecycle, console capture, stdin, crash handling
│   ├── registry.py         instance registry and manager cache
│   ├── java.py             Java discovery, download and install
│   ├── injector.py         authlib-injector download with checksum verification
│   ├── auth_plugin.py      javaagent construction for authlib-injector
│   ├── whitelist.py        whitelist.json generation and sync
│   ├── bans.py             ban sync into banned-players.json
│   ├── status.py           Minecraft server list ping probe
│   ├── pidfile.py          PID tracking for crash-safe process detection
│   ├── download.py         cancellable downloads with progress and speed tracking
│   └── config.py           instance model <-> dict conversion
├── web/
│   ├── admin.py            admin panel API and page rendering
│   ├── __init__.py         projects/news/launcher APIs
│   ├── templates/          server-rendered HTML fragments
│   └── static/             CSS and vanilla JS (charts, file manager, dashboard)
├── modpack_importer.py     CurseForge/Modrinth archive import
└── updater.py              GitHub release check, backup and in-place update
```

`build.py` compiles the whole server into a standalone binary with Nuitka, and optionally produces an Inno Setup installer on Windows.

## Data locations

All runtime data lives under `~/.uroboros/server/`:

| Path | Contents |
| --- | --- |
| `config.json` | Global configuration |
| `auth.db` | SQLite database (users, sessions, bans, instances, projects, modpacks) |
| `servers/<instance-id>/` | Each instance's server directory |
| `pids/` | PID files for running instances |
| `projects/` | Project and modpack file storage |
| `java/` | Managed Java runtimes |
| `server.log` | Log file written by the autostart scripts |

## Updating

```sh
python -m server update --check
python -m server update
```

The updater pulls the latest release from GitHub, creates a zip backup in `.backups/` first, and then syncs the tree. Running instances are left alone by the CLI updater but the panel updater can stop them first and restart them after the swap. A restart of Uroboros itself is required after an update.

## Building from source

`build.py` wraps Nuitka. It resolves prerequisites automatically (installing Nuitka, and on Linux the `patchelf`/compiler/dev-header packages) and generates an icon if none is provided.

```sh
python build.py                # interactive, onedir output in dist/
python build.py --onefile      # single executable
python build.py --installer    # also compile an Inno Setup installer (Windows)
```

The output is a self-contained binary that needs no Python installation on the target machine. `run_server_compiled.bat` / `run_server_compiled.sh` start it the same way the source scripts do.

## Notes

- Instances and projects were previously stored as JSON files and are migrated into SQLite automatically on startup; the original files are kept as `.bak`.
- `auth.db` is the single source of truth for accounts. Deleting it resets all accounts, so back it up before reinstalling.
- The auth API rate-limits to 10 requests per minute per IP by default; the panel login allows 5 attempts per 5 minutes. Both are configurable in code.
