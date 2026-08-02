#!/usr/bin/env bash

cd "$(dirname "$0")"

PY=""
RUNNER=""
USER_MODE=""

ensure_python() {
    if [ -n "$PY" ]; then
        return 0
    fi
    if command -v python3.13 >/dev/null 2>&1; then
        PY="$(command -v python3.13)"
        return 0
    fi
    if command -v python3 >/dev/null 2>&1; then
        PY="$(command -v python3)"
        return 0
    fi
    echo "[INFO] Python 3 not found. Installing ..."
    if command -v apt-get >/dev/null 2>&1; then
        sudo apt-get update >/dev/null 2>&1
        sudo apt-get install -y python3.13 python3.13-venv python3-pip >/dev/null 2>&1 \
            || sudo apt-get install -y python3 python3-venv python3-pip >/dev/null 2>&1
    elif command -v dnf >/dev/null 2>&1; then
        sudo dnf install -y python3.13 python3-pip >/dev/null 2>&1 \
            || sudo dnf install -y python3 python3-pip >/dev/null 2>&1
    elif command -v yum >/dev/null 2>&1; then
        sudo yum install -y python3 python3-pip >/dev/null 2>&1
    elif command -v pacman >/dev/null 2>&1; then
        sudo pacman -Sy --noconfirm python python-pip >/dev/null 2>&1
    elif command -v zypper >/dev/null 2>&1; then
        sudo zypper install -y python3 python3-pip >/dev/null 2>&1
    else
        echo "[ERROR] No supported package manager found. Install Python 3 manually."
        return 1
    fi
    if command -v python3.13 >/dev/null 2>&1; then
        PY="$(command -v python3.13)"
        return 0
    fi
    if command -v python3 >/dev/null 2>&1; then
        PY="$(command -v python3)"
        return 0
    fi
    echo "[ERROR] Python installation failed. Install Python 3 manually."
    return 1
}

ensure_venv() {
    RUNNER=""
    USER_MODE=""
    if [ ! -f ".venv/bin/python" ]; then
        echo "[INFO] Creating virtual environment ..."
        "$PY" -m venv .venv >/dev/null 2>&1 || true
    fi
    if [ -f ".venv/bin/python" ]; then
        RUNNER=".venv/bin/python"
    else
        echo "[WARN] Could not create venv. Using system Python."
        echo "       Packages will be installed for the current user only."
        RUNNER="$PY"
        USER_MODE="1"
    fi
    if "$RUNNER" -c "import fastapi,uvicorn,sqlalchemy,aiosqlite,pydantic,aiohttp,requests,psutil,multipart" >/dev/null 2>&1; then
        return 0
    fi
    echo "[INFO] Installing dependencies (first run) ..."
    if [ -n "$USER_MODE" ]; then
        "$PY" -m pip install --user --upgrade pip >/dev/null 2>&1
        "$PY" -m pip install --user -r requirements.txt || {
            echo "[ERROR] Failed to install dependencies."
            return 1
        }
        return 0
    fi
    "$RUNNER" -m pip install --upgrade pip >/dev/null 2>&1
    if "$RUNNER" -m pip install -r requirements.txt >/dev/null 2>&1; then
        return 0
    fi
    echo "[WARN] Install into venv failed. Trying --user ..."
    RUNNER="$PY"
    USER_MODE="1"
    "$PY" -m pip install --user -r requirements.txt || {
        echo "[ERROR] Failed to install dependencies."
        return 1
    }
}

killport() {
    local port="$1"
    echo "Freeing port $port ..."
    if command -v fuser >/dev/null 2>&1; then
        fuser -k "$port"/tcp >/dev/null 2>&1 || true
    elif command -v lsof >/dev/null 2>&1; then
        local pids
        pids="$(lsof -ti tcp:"$port" 2>/dev/null || true)"
        for pid in $pids; do
            kill -9 "$pid" 2>/dev/null && echo "Killed process PID $pid"
        done
    else
        echo "Warning: fuser/lsof not found, cannot free port $port"
    fi
}

run_server() {
    killport 25581
    echo
    echo "Starting server at: http://127.0.0.1:25581"
    echo "Admin panel at: http://127.0.0.1:25581/admin/"
    echo
    "$RUNNER" -m server run
}

build_version() {
    echo
    echo "Building compiled version with Nuitka ..."
    echo "This runs build.py using the virtual environment."
    echo "This can take several minutes."
    echo
    if ! "$RUNNER" -c "import nuitka" >/dev/null 2>&1; then
        echo "[INFO] Installing Nuitka into the virtual environment ..."
        if [ -n "$USER_MODE" ]; then
            if ! "$RUNNER" -m pip install --user nuitka; then
                echo "[ERROR] Failed to install Nuitka."
                return 1
            fi
        else
            if ! "$RUNNER" -m pip install nuitka; then
                echo "[ERROR] Failed to install Nuitka."
                return 1
            fi
        fi
    fi
    if ! "$RUNNER" build.py; then
        echo
        echo "[ERROR] Build failed. Fix the errors above and try again."
        return 1
    fi
    echo
    echo "[OK] Build finished. Use run_server_compiled.sh to run the compiled version."
}

add_autostart() {
    local dir="$HOME/.config/autostart"
    local script
    script="$(cd "$(dirname "$0")" && pwd)/$(basename "$0")"
    mkdir -p "$dir"
    cat > "$dir/uroboros-server.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=Uroboros Server
Exec="$script" --autostart
Terminal=false
X-GNOME-Autostart-enabled=true
EOF
    echo "[OK] Autostart enabled."
}

remove_autostart() {
    rm -f "$HOME/.config/autostart/uroboros-server.desktop"
    echo "[OK] Autostart disabled."
}

hold() {
    if [ -z "$AUTOSTART" ]; then
        read -n 1 -s -r -p "Press any key to continue..."
        echo
    fi
}

AUTOSTART=""
if [ "$1" = "--autostart" ]; then
    AUTOSTART="1"
fi

if [ -n "$AUTOSTART" ]; then
    ensure_python || exit 1
    ensure_venv || exit 1
    killport 25581 >/dev/null 2>&1
    nohup "$RUNNER" -m server run >> "server.log" 2>&1 &
    exit 0
fi

ensure_python || exit 1
ensure_venv || exit 1

while true; do
    clear
    echo "================================================"
    echo "              UROBOROS SERVER"
    echo "================================================"
    echo
    echo " [1] Run admin web panel"
    echo " [2] Start Minecraft server"
    echo " [3] Stop Minecraft server"
    echo " [4] Show server status"
    echo " [S] Full shutdown (panel + Minecraft)"
    echo " [B] Build compiled version (Nuitka)"
    echo " [A] Enable autostart"
    echo " [R] Disable autostart"
    echo " [Q] Quit"
    echo
    read -n 1 -p "Press a key to select: " choice
    echo
    case "$choice" in
        1)
            run_server
            hold
            ;;
        2)
            echo
            echo "Starting Minecraft server ..."
            echo
            "$RUNNER" -m server start
            hold
            ;;
        3)
            echo
            echo "Stopping Minecraft server ..."
            echo
            "$RUNNER" -m server stop
            hold
            ;;
        4)
            echo
            echo "Server status:"
            echo
            "$RUNNER" -m server status
            hold
            ;;
        s|S)
            echo
            echo "Full shutdown ..."
            killport 25581
            echo
            echo "Stopping Minecraft servers ..."
            "$RUNNER" -m server stop
            echo
            echo "[OK] Everything stopped."
            hold
            ;;
        b|B)
            build_version
            hold
            ;;
        a|A)
            add_autostart
            hold
            ;;
        r|R)
            remove_autostart
            hold
            ;;
        q|Q)
            exit 0
            ;;
        *)
            echo "Invalid choice"
            hold
            ;;
    esac
done
