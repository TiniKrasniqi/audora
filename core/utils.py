
# -*- coding: utf-8 -*-
import os
import sys
import shutil
from datetime import datetime

DEFAULT_BITRATE = "192"
JS_RUNTIME_COMMANDS = {
    "deno": "deno",
    "node": "node",
    "bun": "bun",
    "quickjs": "qjs",
}

def human_time():
    return datetime.now().strftime("%H:%M:%S")

def ensure_ffmpeg_or_die(root=None):
    if not shutil.which("ffmpeg"):
        # Avoid importing tkinter here to keep utils lightweight.
        # Let caller decide how to show error dialogs.
        msg = "FFmpeg is not on PATH. Install FFmpeg and restart the app."
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

    We prefer keeping everything inside a dedicated "YouTubeDownloader" directory
    beneath the user's Music folder (if available). If we cannot create that
    directory, gracefully fall back to a similar folder inside Downloads, and as a
    last resort, a "downloads" directory inside the current working directory.
    """

    home = os.path.expanduser("~")
    candidates = [
        os.path.join(home, "Music", "YouTubeDownloader"),
        os.path.join(home, "Downloads", "YouTubeDownloader"),
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
