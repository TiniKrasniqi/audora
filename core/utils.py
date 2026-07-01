
# -*- coding: utf-8 -*-
from __future__ import annotations

import os
import sys
import shutil
from datetime import datetime
from pathlib import Path
from typing import Iterable, Optional

DEFAULT_BITRATE = "192"
FFMPEG_RUNTIME_DIR_NAME = "ffmpeg"
JS_RUNTIME_COMMANDS = {
    "deno": "deno",
    "node": "node",
    "bun": "bun",
    "quickjs": "qjs",
}

def human_time():
    return datetime.now().strftime("%H:%M:%S")

def audora_runtime_root() -> Path:
    if sys.platform.startswith("win"):
        base_dir = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA") or os.path.expanduser("~")
    elif sys.platform == "darwin":
        base_dir = os.path.join(os.path.expanduser("~"), "Library", "Application Support")
    else:
        base_dir = os.environ.get("XDG_DATA_HOME") or os.path.join(os.path.expanduser("~"), ".local", "share")
    return Path(base_dir) / "Audora" / "runtime"

def _runtime_executable_name(name: str) -> str:
    return f"{name}.exe" if sys.platform.startswith("win") else name

def _candidate_ffmpeg_dirs() -> Iterable[Path]:
    env_dir = os.environ.get("AUDORA_FFMPEG_DIR")
    if env_dir:
        env_path = Path(env_dir)
        yield env_path / "bin"
        yield env_path

    runtime_root = audora_runtime_root()
    yield runtime_root / FFMPEG_RUNTIME_DIR_NAME / "bin"
    yield runtime_root / FFMPEG_RUNTIME_DIR_NAME

    if runtime_root.exists():
        for candidate in sorted(runtime_root.glob("ffmpeg*"), reverse=True):
            yield candidate / "bin"
            yield candidate

def is_usable_ffmpeg_bin_dir(bin_dir: Path) -> bool:
    return (
        bin_dir.is_dir()
        and (bin_dir / _runtime_executable_name("ffmpeg")).is_file()
        and (bin_dir / _runtime_executable_name("ffprobe")).is_file()
    )

def find_ffmpeg_bin_dir() -> Optional[Path]:
    seen = set()
    for candidate in _candidate_ffmpeg_dirs():
        try:
            resolved = candidate.expanduser().resolve()
        except OSError:
            continue
        if resolved in seen:
            continue
        seen.add(resolved)
        if is_usable_ffmpeg_bin_dir(resolved):
            return resolved

    ffmpeg_path = shutil.which("ffmpeg")
    ffprobe_path = shutil.which("ffprobe")
    if ffmpeg_path and ffprobe_path:
        try:
            return Path(ffmpeg_path).resolve().parent
        except OSError:
            return Path(ffmpeg_path).parent
    return None

def configure_ffmpeg_runtime(ffmpeg_dir: Optional[Path | str] = None) -> Optional[Path]:
    if ffmpeg_dir:
        root = Path(ffmpeg_dir).expanduser()
        candidates = [root, root / "bin"]
        resolved = next((candidate.resolve() for candidate in candidates if is_usable_ffmpeg_bin_dir(candidate)), None)
    else:
        resolved = find_ffmpeg_bin_dir()

    if resolved is None:
        return None

    resolved_text = str(resolved)
    path_parts = os.environ.get("PATH", "").split(os.pathsep)
    if resolved_text not in path_parts:
        os.environ["PATH"] = resolved_text + os.pathsep + os.environ.get("PATH", "")
    os.environ["AUDORA_FFMPEG_DIR"] = resolved_text
    return resolved

def ensure_ffmpeg_or_die(root=None):
    configure_ffmpeg_runtime()
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        # Avoid importing tkinter here to keep utils lightweight.
        # Let caller decide how to show error dialogs.
        msg = "FFmpeg is not available. Re-run the Audora installer or install FFmpeg and restart the app."
        if root is not None:
            try:
                from tkinter import messagebox
                messagebox.showerror("FFmpeg missing", msg)
            except Exception:
                pass
            try:
                root.destroy()
            except Exception:
                pass
        sys.exit(1)

def default_download_dir():
    """Return the default folder used by the app to store downloads.

    New installs use a dedicated "Audora" directory beneath the user's Music
    folder when possible. Existing legacy "YouTubeDownloader" folders are still
    respected so older users keep seeing the files they already downloaded.
    """

    home = os.path.expanduser("~")
    legacy_candidates = [
        os.path.join(home, "Music", "YouTubeDownloader"),
        os.path.join(home, "Downloads", "YouTubeDownloader"),
    ]
    for path in legacy_candidates:
        try:
            if os.path.isdir(path):
                with os.scandir(path) as entries:
                    if any(entries):
                        return path
        except OSError:
            continue

    candidates = [
        os.path.join(home, "Music", "Audora"),
        os.path.join(home, "Downloads", "Audora"),
    ]

    for path in candidates:
        try:
            os.makedirs(path, exist_ok=True)
            return path
        except OSError:
            continue

    fallback = os.path.join(os.getcwd(), "downloads")
    try:
        os.makedirs(fallback, exist_ok=True)
        return fallback
    except OSError:
        # If even this fails, return the current working directory without
        # attempting further creations.
        return os.getcwd()

def is_likely_playlist_url(url: str) -> bool:
    if not url:
        return False
    u = url.lower()
    return ("list=" in u) or ("/playlist" in u) or ("music.youtube.com/playlist" in u)

def available_js_runtimes():
    """Return yt-dlp JavaScript runtimes available on PATH.

    Recent yt-dlp versions can use JavaScript runtimes for some YouTube
    extraction paths. Only Deno is enabled by default in yt-dlp, so explicitly
    passing the installed runtimes lets users benefit from Node/Bun/QuickJS too.
    """

    runtimes = {}
    for name, executable in JS_RUNTIME_COMMANDS.items():
        path = shutil.which(executable)
        if path:
            runtimes[name] = {"path": path}
    return runtimes

def js_runtime_warning_message() -> str:
    return (
        "No supported JavaScript runtime was found on PATH. Downloads may still "
        "work, but YouTube extraction is less reliable with current yt-dlp. "
        "Install Deno 2.3+, Node.js 22+, Bun 1.2.11+, or QuickJS, then restart "
        "the app."
    )
