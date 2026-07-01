#!/usr/bin/env bash
set -euo pipefail

# Bootstraps the Audora application on Unix-like systems.
# The script ensures Python is available, creates a virtual environment,
# installs the dependencies and generates a helper script for launching
# the application.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_DIR="$SCRIPT_DIR/app"
VENV_DIR="$SCRIPT_DIR/venv"

PYTHON_CMD=""

if command -v python3 >/dev/null 2>&1; then
    PYTHON_CMD="python3"
elif command -v python >/dev/null 2>&1; then
    PYTHON_CMD="python"
fi

if [[ -z "$PYTHON_CMD" ]]; then
    echo "Python 3 is required. Attempting to install with available package managers..."
    if command -v apt-get >/dev/null 2>&1; then
        sudo apt-get update
        sudo apt-get install -y python3 python3-venv python3-pip
        PYTHON_CMD="python3"
    elif command -v brew >/dev/null 2>&1; then
        brew install python@3.11
        PYTHON_CMD="python3"
    else
        echo "Unable to automatically install Python. Please install Python 3.8+ manually and rerun this script." >&2
        exit 1
    fi
fi

install_vlc_runtime() {
    if command -v vlc >/dev/null 2>&1; then
        return
    fi

    echo "Installing VLC playback runtime..."
    if command -v apt-get >/dev/null 2>&1; then
        sudo apt-get update
        sudo apt-get install -y vlc
    elif command -v brew >/dev/null 2>&1; then
        brew install --cask vlc || brew install vlc
    else
        cat >&2 <<'VLC_WARNING'
Warning: VLC/libVLC was not found and could not be installed automatically.
Install VLC with your system package manager before using the in-app player.
VLC_WARNING
    fi
}

install_ffmpeg_runtime() {
    if command -v ffmpeg >/dev/null 2>&1 && command -v ffprobe >/dev/null 2>&1; then
        return
    fi

    echo "Installing FFmpeg runtime..."
    if command -v apt-get >/dev/null 2>&1; then
        sudo apt-get update
        sudo apt-get install -y ffmpeg
    elif command -v brew >/dev/null 2>&1; then
        brew install ffmpeg
    else
        cat >&2 <<'FFMPEG_WARNING'
Warning: FFmpeg/ffprobe was not found and could not be installed automatically.
Install FFmpeg with your system package manager before downloading or probing media.
FFMPEG_WARNING
    fi
}

if [[ ! -d "$VENV_DIR" ]]; then
    echo "Creating virtual environment..."
    "$PYTHON_CMD" -m venv "$VENV_DIR"
fi

# shellcheck disable=SC1090
source "$VENV_DIR/bin/activate"

python -m pip install --upgrade pip
python -m pip install -r "$APP_DIR/requirements.txt"

install_vlc_runtime
install_ffmpeg_runtime

if ! command -v deno >/dev/null 2>&1 \
    && ! command -v node >/dev/null 2>&1 \
    && ! command -v bun >/dev/null 2>&1 \
    && ! command -v qjs >/dev/null 2>&1; then
    cat >&2 <<'RUNTIME_WARNING'
Warning: no JavaScript runtime was found on PATH.
The app can still run, but current yt-dlp works best for YouTube when Deno 2.3+,
Node.js 22+, Bun 1.2.11+, or QuickJS is installed.
RUNTIME_WARNING
fi

deactivate

cat > "$SCRIPT_DIR/run_app.sh" <<'LAUNCHER'
#!/usr/bin/env bash
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/venv/bin/activate"
python "$SCRIPT_DIR/app/main.py" "$@"
deactivate
LAUNCHER

chmod +x "$SCRIPT_DIR/run_app.sh"

echo "Installation complete. Run './run_app.sh' to launch the application."
