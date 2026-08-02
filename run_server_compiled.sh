#!/usr/bin/env bash

cd "$(dirname "$0")"

EXE=""
for cand in "dist/UroborosServer" "dist/UroborosServer.bin" "dist/UroborosServer.dist/UroborosServer" "UroborosServer" "UroborosServer.bin"; do
    if [ -x "$cand" ]; then
        EXE="$cand"
        break
    fi
done

if [ -z "$EXE" ]; then
    echo "[ERROR] Compiled server not found."
    echo "Run run_server.sh and press [B] to build it."
    read -n 1 -s -r -p "Press any key to continue..."
    exit 1
fi

killport() {
    local port="$1"
    echo "Killing existing process on port $port ..."
    if command -v fuser >/dev/null 2>&1; then
        fuser -k "$port"/tcp >/dev/null 2>&1 || true
    elif command -v lsof >/dev/null 2>&1; then
        local pids
        pids="$(lsof -ti tcp:"$port" 2>/dev/null || true)"
        for pid in $pids; do
            kill -9 "$pid" 2>/dev/null && echo "Killed PID $pid on port $port"
        done
    else
        echo "Warning: fuser/lsof not found, cannot free port $port"
    fi
}

run_server() {
    killport 25581
    echo
    echo "Starting server at http://127.0.0.1:25581 ..."
    echo "Admin dashboard: http://127.0.0.1:25581/admin/"
    echo
    "$EXE" run
}

add_autostart() {
    local dir="$HOME/.config/autostart"
    local script
    script="$(cd "$(dirname "$0")" && pwd)/$(basename "$0")"
    mkdir -p "$dir"
    cat > "$dir/uroboros-server-compiled.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=Uroboros Server (Compiled)
Exec="$script" --autostart
Terminal=false
X-GNOME-Autostart-enabled=true
EOF
    echo "Autostart enabled."
}

remove_autostart() {
    rm -f "$HOME/.config/autostart/uroboros-server-compiled.desktop"
    echo "Autostart disabled."
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
    killport 25581 >/dev/null 2>&1
    nohup "$EXE" run >> "server.log" 2>&1 &
    exit 0
fi

VERSION="$("$EXE" version 2>/dev/null)"

while true; do
    clear
    echo ">> Uroboros Server (compiled) - v$VERSION <<"
    echo
    echo "[1] Run full server (auth + admin dashboard)"
    echo "[2] Start Minecraft server only"
    echo "[3] Stop Minecraft server"
    echo "[4] Show server status"
    echo "[S] Full shutdown (panel + Minecraft)"
    echo "[A] Add to autostart"
    echo "[R] Remove from autostart"
    echo "[Q] Quit"
    echo
    read -n 1 -p "Select action: " choice
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
            "$EXE" start
            hold
            ;;
        3)
            echo
            "$EXE" stop
            hold
            ;;
        4)
            echo
            "$EXE" status
            hold
            ;;
        s|S)
            echo
            echo "Full shutdown ..."
            killport 25581
            echo
            echo "Stopping Minecraft servers ..."
            "$EXE" stop
            echo
            echo "[OK] Everything stopped."
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
