# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import importlib
import os
import platform
import shutil
import struct
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional


VLC_VERSION = "3.0.23"
VLC_RUNTIME_DIR_NAME = f"vlc-{VLC_VERSION}"
VLC_RUNTIME_ARCHIVE = f"{VLC_RUNTIME_DIR_NAME}-win64.zip"
VLC_RUNTIME_URL = (
    f"https://download.videolan.org/pub/videolan/vlc/{VLC_VERSION}/win64/{VLC_RUNTIME_ARCHIVE}"
)
VLC_RUNTIME_SHA256 = "992d19dbd0b8a7cde9167d2f7780b1ef6f92acc8a71acfa736101a21f35181e1"

_DLL_DIRECTORY_HANDLES = []


@dataclass
class MediaInfo:
    duration: float = 0.0
    has_audio: bool = False
    has_video: bool = False
    width: int = 0
    height: int = 0
    fps: float = 30.0


def format_media_time(seconds: Optional[float]) -> str:
    total = max(0, int(seconds or 0))
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


def _hidden_startupinfo():
    if not sys.platform.startswith("win"):
        return None
    startupinfo = subprocess.STARTUPINFO()
    startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    return startupinfo


def _parse_frame_rate(value: object) -> float:
    if not value:
        return 30.0
    text = str(value)
    try:
        if "/" in text:
            numerator, denominator = text.split("/", 1)
            denominator_float = float(denominator)
            if denominator_float:
                return max(1.0, min(120.0, float(numerator) / denominator_float))
        return max(1.0, min(120.0, float(text)))
    except (TypeError, ValueError):
        return 30.0


def probe_media(file_path: str) -> MediaInfo:
    if not file_path or not os.path.exists(file_path) or not shutil.which("ffprobe"):
        return MediaInfo()

    command = [
        "ffprobe",
        "-v",
        "error",
        "-print_format",
        "json",
        "-show_entries",
        "format=duration:stream=codec_type,width,height,avg_frame_rate,r_frame_rate:stream_disposition",
        file_path,
    ]
    try:
        result = subprocess.run(
            command,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            startupinfo=_hidden_startupinfo(),
        )
    except (OSError, subprocess.CalledProcessError, ValueError):
        return MediaInfo()

    try:
        payload = json.loads(result.stdout or "{}")
    except json.JSONDecodeError:
        return MediaInfo()

    info = MediaInfo()
    try:
        info.duration = max(0.0, float(payload.get("format", {}).get("duration") or 0.0))
    except (TypeError, ValueError):
        info.duration = 0.0

    for stream in payload.get("streams", []) or []:
        codec_type = stream.get("codec_type")
        if codec_type == "audio":
            info.has_audio = True
        elif codec_type == "video":
            disposition = stream.get("disposition") or {}
            if disposition.get("attached_pic"):
                continue
            info.has_video = True
            try:
                info.width = max(info.width, int(stream.get("width") or 0))
                info.height = max(info.height, int(stream.get("height") or 0))
            except (TypeError, ValueError):
                pass
            info.fps = _parse_frame_rate(stream.get("avg_frame_rate") or stream.get("r_frame_rate"))
    return info


def audora_runtime_root() -> Path:
    if sys.platform.startswith("win"):
        base_dir = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA") or os.path.expanduser("~")
    elif sys.platform == "darwin":
        base_dir = os.path.join(os.path.expanduser("~"), "Library", "Application Support")
    else:
        base_dir = os.environ.get("XDG_DATA_HOME") or os.path.join(os.path.expanduser("~"), ".local", "share")
    return Path(base_dir) / "Audora" / "runtime"


def _candidate_vlc_dirs() -> Iterable[Path]:
    env_dir = os.environ.get("AUDORA_VLC_DIR")
    if env_dir:
        yield Path(env_dir)

    runtime_root = audora_runtime_root()
    yield runtime_root / VLC_RUNTIME_DIR_NAME
    yield runtime_root / f"{VLC_RUNTIME_DIR_NAME}-win64" / VLC_RUNTIME_DIR_NAME

    if runtime_root.exists():
        for candidate in sorted(runtime_root.glob("vlc-*"), reverse=True):
            yield candidate
            nested = candidate / candidate.name.replace("-win64", "")
            yield nested

    if sys.platform.startswith("win"):
        for env_name in ("ProgramFiles", "ProgramW6432", "ProgramFiles(x86)"):
            base_dir = os.environ.get(env_name)
            if base_dir:
                yield Path(base_dir) / "VideoLAN" / "VLC"
    else:
        vlc_binary = shutil.which("vlc")
        if vlc_binary:
            yield Path(vlc_binary).resolve().parent


def _dll_machine_type(dll_path: Path) -> Optional[int]:
    try:
        with dll_path.open("rb") as dll_file:
            if dll_file.read(2) != b"MZ":
                return None
            dll_file.seek(0x3C)
            pe_offset = struct.unpack("<I", dll_file.read(4))[0]
            dll_file.seek(pe_offset)
            if dll_file.read(4) != b"PE\0\0":
                return None
            return struct.unpack("<H", dll_file.read(2))[0]
    except (OSError, struct.error):
        return None


def _runtime_matches_python(vlc_dir: Path) -> bool:
    if not sys.platform.startswith("win"):
        return True

    machine_type = _dll_machine_type(vlc_dir / "libvlc.dll")
    if machine_type is None:
        return False

    python_bits = struct.calcsize("P") * 8
    machine_bits = {
        0x014C: 32,  # IMAGE_FILE_MACHINE_I386
        0x8664: 64,  # IMAGE_FILE_MACHINE_AMD64
        0xAA64: 64,  # IMAGE_FILE_MACHINE_ARM64
    }.get(machine_type)
    return machine_bits == python_bits


def is_usable_vlc_runtime(vlc_dir: Path) -> bool:
    return (
        vlc_dir.is_dir()
        and (vlc_dir / "libvlc.dll").is_file()
        and (vlc_dir / "plugins").is_dir()
        and _runtime_matches_python(vlc_dir)
    )


def find_vlc_runtime_dir() -> Optional[Path]:
    seen = set()
    for candidate in _candidate_vlc_dirs():
        try:
            resolved = candidate.expanduser().resolve()
        except OSError:
            continue
        if resolved in seen:
            continue
        seen.add(resolved)
        if is_usable_vlc_runtime(resolved):
            return resolved
    return None


def configure_vlc_runtime(vlc_dir: Optional[Path | str] = None) -> Path:
    resolved = Path(vlc_dir).expanduser().resolve() if vlc_dir else find_vlc_runtime_dir()
    if resolved is None:
        raise RuntimeError(
            "64-bit VLC playback runtime was not found. Re-run the installer or install 64-bit VLC."
        )
    if not is_usable_vlc_runtime(resolved):
        raise RuntimeError(f"VLC runtime is not usable for this Python build: {resolved}")

    if sys.platform.startswith("win") and hasattr(os, "add_dll_directory"):
        handle = os.add_dll_directory(str(resolved))
        _DLL_DIRECTORY_HANDLES.append(handle)

    resolved_text = str(resolved)
    path_parts = os.environ.get("PATH", "").split(os.pathsep)
    if resolved_text not in path_parts:
        os.environ["PATH"] = resolved_text + os.pathsep + os.environ.get("PATH", "")

    plugins_dir = resolved / "plugins"
    if plugins_dir.is_dir():
        os.environ["VLC_PLUGIN_PATH"] = str(plugins_dir)
    os.environ["AUDORA_VLC_DIR"] = resolved_text
    return resolved


def _candidate_vlc_binding_paths() -> Iterable[Path]:
    env_path = os.environ.get("AUDORA_PYTHON_VLC_PATH")
    if env_path:
        yield Path(env_path)

    project_root = Path(__file__).resolve().parents[1]
    for venv_name in (".venv", "venv"):
        venv_dir = project_root / venv_name
        yield venv_dir / "Lib" / "site-packages" / "vlc.py"
        lib_dir = venv_dir / "lib"
        if lib_dir.is_dir():
            yield from lib_dir.glob("python*/site-packages/vlc.py")


def import_vlc_binding(vlc_dir: Optional[Path | str] = None):
    runtime_dir = configure_vlc_runtime(vlc_dir)
    last_error: Optional[BaseException] = None

    search_roots: list[Optional[Path]] = [None]
    for candidate in _candidate_vlc_binding_paths():
        if candidate.is_file():
            search_roots.append(candidate.parent)

    seen_roots = set()
    for root in search_roots:
        if root is not None:
            try:
                root = root.resolve()
            except OSError:
                continue
            if root in seen_roots:
                continue
            seen_roots.add(root)
            root_text = str(root)
            if root_text not in sys.path:
                sys.path.insert(0, root_text)

        sys.modules.pop("vlc", None)
        try:
            return importlib.import_module("vlc"), runtime_dir
        except ModuleNotFoundError as exc:
            if exc.name != "vlc":
                raise
            last_error = exc
        except FileNotFoundError as exc:
            last_error = exc

    detail = f": {last_error}" if last_error else ""
    raise RuntimeError(
        "The python-vlc binding is not installed for this Python interpreter. "
        "Install dependencies with 'python -m pip install -r requirements.txt' or run the installer"
        f"{detail}"
    )


def vlc_runtime_status() -> str:
    runtime_dir = find_vlc_runtime_dir()
    if runtime_dir is not None:
        return str(runtime_dir)
    system = platform.system() or sys.platform
    return f"No compatible VLC runtime found for {system} {struct.calcsize('P') * 8}-bit Python."
