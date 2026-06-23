# -*- coding: utf-8 -*-
from __future__ import annotations

import base64
import io
import json
import mimetypes
import os
import queue
import shutil
import sys
import threading
from dataclasses import asdict
from datetime import datetime
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Dict, Iterable, List, Optional
from urllib.request import urlopen
from urllib.parse import parse_qs, quote, unquote, urlparse

from PIL import Image, ImageDraw

from core.downloader import DownloadProgress
from core.media import format_media_time, probe_media
from core.queue import DownloadManager
from core.sync import AudoraSyncServer, DEFAULT_SYNC_PORT
from core.utils import DEFAULT_BITRATE, default_download_dir, human_time


AUDIO_QUALITIES = {
    "128 kbps": "128",
    "192 kbps": "192",
    "256 kbps": "256",
    "320 kbps": "320",
}

VIDEO_QUALITIES = ["480p", "720p", "1080p", "1440p", "2160p (4K)"]
MEDIA_EXTENSIONS = (".mp3", ".m4a", ".wav", ".flac", ".aac", ".ogg", ".mp4", ".webm", ".mkv", ".mov", ".avi", ".m4v")
SETTINGS_FILE_NAME = "settings.json"


def _config_dir(name: str) -> str:
    if sys.platform.startswith("win"):
        base_dir = os.environ.get("APPDATA") or os.path.expanduser("~")
    elif sys.platform == "darwin":
        base_dir = os.path.join(os.path.expanduser("~"), "Library", "Application Support")
    else:
        base_dir = os.environ.get("XDG_CONFIG_HOME") or os.path.join(os.path.expanduser("~"), ".config")
    path = os.path.join(base_dir, name)
    os.makedirs(path, exist_ok=True)
    return path


def _settings_path() -> str:
    return os.path.join(_config_dir("Audora"), SETTINGS_FILE_NAME)


def _legacy_settings_path() -> str:
    return os.path.join(_config_dir("YouTubeDownloader"), SETTINGS_FILE_NAME)


def _default_settings() -> Dict[str, str]:
    return {
        "audio_quality": "192 kbps",
        "video_quality": "1080p",
        "parallel_downloads": "3",
        "download_dir": default_download_dir(),
    }


def _clean_settings(raw_settings: Optional[Dict]) -> Dict[str, str]:
    defaults = _default_settings()
    settings = dict(defaults)
    if isinstance(raw_settings, dict):
        settings.update({key: str(value) for key, value in raw_settings.items() if value is not None})

    if settings.get("audio_quality") not in AUDIO_QUALITIES:
        settings["audio_quality"] = defaults["audio_quality"]
    if settings.get("video_quality") not in VIDEO_QUALITIES:
        settings["video_quality"] = defaults["video_quality"]

    try:
        parallel = int(settings.get("parallel_downloads", defaults["parallel_downloads"]))
    except (TypeError, ValueError):
        parallel = int(defaults["parallel_downloads"])
    settings["parallel_downloads"] = str(max(1, min(8, parallel)))

    download_dir = (settings.get("download_dir") or "").strip()
    settings["download_dir"] = download_dir or defaults["download_dir"]
    return settings


def _load_settings() -> Dict[str, str]:
    for path in (_settings_path(), _legacy_settings_path()):
        try:
            with open(path, "r", encoding="utf-8") as settings_file:
                return _clean_settings(json.load(settings_file))
        except (OSError, ValueError, json.JSONDecodeError):
            continue
    return _default_settings()


def _save_settings(settings: Dict[str, str]) -> Dict[str, str]:
    cleaned = _clean_settings(settings)
    with open(_settings_path(), "w", encoding="utf-8") as settings_file:
        json.dump(cleaned, settings_file, indent=2)
    return cleaned


def _format_bytes(value: Optional[int | float]) -> str:
    try:
        amount = float(value or 0)
    except (TypeError, ValueError):
        amount = 0.0
    units = ["B", "KB", "MB", "GB", "TB"]
    index = 0
    while amount >= 1024 and index < len(units) - 1:
        amount /= 1024
        index += 1
    if index == 0:
        return f"{int(amount)} {units[index]}"
    return f"{amount:.1f} {units[index]}"


def _format_eta(seconds: Optional[int]) -> str:
    if seconds is None:
        return ""
    try:
        seconds = max(0, int(seconds))
    except (TypeError, ValueError):
        return ""
    minutes, sec = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours:02d}:{minutes:02d}:{sec:02d}"
    return f"{minutes:02d}:{sec:02d}"


def _timestamp(mtime: Optional[float]) -> str:
    if not mtime:
        return ""
    try:
        return datetime.fromtimestamp(mtime).strftime("%H:%M %d.%m.%Y")
    except (ValueError, OSError):
        return ""


def _path_uri(path: Optional[str]) -> str:
    if not path:
        return ""
    try:
        return Path(path).resolve().as_uri()
    except (OSError, ValueError):
        return ""


def _image_data_uri(path: Optional[str], size: int = 220) -> str:
    if not path:
        return ""
    try:
        with Image.open(path) as image:
            image = image.convert("RGB")
            image.thumbnail((size, size))
            buffer = io.BytesIO()
            image.save(buffer, format="PNG")
        return "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode("ascii")
    except Exception:  # pylint: disable=broad-except
        return ""


def _find_thumbnail(file_path: str) -> Optional[str]:
    base, _ = os.path.splitext(file_path)
    for ext in (".jpg", ".jpeg", ".png", ".webp"):
        candidate = base + ext
        if os.path.exists(candidate):
            return candidate
    return None


def _sidecars_for_file(file_path: str) -> List[str]:
    base, _ = os.path.splitext(file_path)
    return [candidate for ext in (".jpg", ".jpeg", ".png", ".webp") if os.path.exists((candidate := base + ext))]


def _is_media_file(path: str) -> bool:
    return path.lower().endswith(MEDIA_EXTENSIONS)


def _safe_joined(root: str, target: str) -> bool:
    try:
        root_norm = os.path.normcase(os.path.abspath(root))
        target_norm = os.path.normcase(os.path.abspath(target))
        return target_norm == root_norm or target_norm.startswith(root_norm + os.sep)
    except (TypeError, ValueError, OSError):
        return False


class _QuietStaticHandler(SimpleHTTPRequestHandler):
    def log_message(self, _format: str, *args) -> None:
        return


def _parse_range_header(value: str, size: int) -> Optional[tuple[int, int]]:
    if not value.startswith("bytes=") or size <= 0:
        return None
    spec = value.removeprefix("bytes=").split(",", 1)[0].strip()
    if "-" not in spec:
        return None
    start_text, end_text = spec.split("-", 1)
    try:
        if start_text:
            start = int(start_text)
            end = int(end_text) if end_text else size - 1
        else:
            suffix = int(end_text)
            start = max(size - suffix, 0)
            end = size - 1
    except ValueError:
        return None
    if start < 0 or start >= size:
        return None
    return start, min(end, size - 1)


def _start_static_server(directory: Path, api: "AudoraWebApi") -> tuple[ThreadingHTTPServer, str]:
    class _AudoraStaticHandler(_QuietStaticHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=str(directory), **kwargs)

        def do_GET(self):  # noqa: N802 - required by SimpleHTTPRequestHandler
            if urlparse(self.path).path == "/media":
                self._serve_media(head_only=False)
                return
            super().do_GET()

        def do_HEAD(self):  # noqa: N802 - required by SimpleHTTPRequestHandler
            if urlparse(self.path).path == "/media":
                self._serve_media(head_only=True)
                return
            super().do_HEAD()

        def _serve_media(self, head_only: bool = False) -> None:
            parsed = urlparse(self.path)
            path = unquote(parse_qs(parsed.query).get("path", [""])[0])
            if not api.can_serve_media(path):
                self.send_error(404)
                return
            try:
                size = os.path.getsize(path)
            except OSError:
                self.send_error(404)
                return

            byte_range = _parse_range_header(self.headers.get("Range", ""), size)
            status = 206 if byte_range else 200
            start, end = byte_range or (0, max(size - 1, 0))
            length = max(end - start + 1, 0)
            mime = mimetypes.guess_type(path)[0] or "application/octet-stream"

            self.send_response(status)
            self.send_header("Content-Type", mime)
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Content-Length", str(length))
            self.send_header("Cache-Control", "no-store")
            if byte_range:
                self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
            self.end_headers()
            if head_only:
                return

            try:
                with open(path, "rb") as media_file:
                    media_file.seek(start)
                    remaining = length
                    while remaining > 0:
                        chunk = media_file.read(min(1024 * 1024, remaining))
                        if not chunk:
                            break
                        self.wfile.write(chunk)
                        remaining -= len(chunk)
            except (BrokenPipeError, ConnectionResetError):
                return

    handler = _AudoraStaticHandler
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    host, port = server.server_address
    api.set_media_base_url(f"http://{host}:{port}")
    thread = threading.Thread(target=server.serve_forever, name="AudoraWebAssets", daemon=True)
    thread.start()
    return server, f"http://{host}:{port}/index.html"


class AudoraWebApi:
    def __init__(self):
        self.settings = _load_settings()
        self._lock = threading.RLock()
        self._event_queue: queue.Queue[dict] = queue.Queue()
        self._logs: List[str] = []
        self._jobs: Dict[str, dict] = {}
        self._terminal_status: dict = {}
        self._sync_activity: List[str] = []
        self._last_qr = ""
        self._last_pairing_payload: Optional[dict] = None
        self._media_base_url = ""

        self.manager = DownloadManager(
            self._enqueue_log,
            self._enqueue_progress,
            max_workers=int(self.settings.get("parallel_downloads") or 3),
        )
        self.sync_server: Optional[AudoraSyncServer] = None
        self.sync_error = ""
        self._start_sync_server()

    def set_media_base_url(self, base_url: str) -> None:
        self._media_base_url = base_url.rstrip("/")

    def can_serve_media(self, path: str) -> bool:
        if not path or not os.path.isfile(path) or not _is_media_file(path):
            return False
        for root in self._download_dirs():
            if _safe_joined(root, path):
                return True
        return False

    def _media_url(self, path: str) -> str:
        if not self._media_base_url or not self.can_serve_media(path):
            return ""
        return f"{self._media_base_url}/media?path={quote(os.path.abspath(path), safe='')}"

    def _download_dirs(self) -> List[str]:
        directory = (self.settings.get("download_dir") or "").strip() or default_download_dir()
        return [directory] if directory else []

    def _start_sync_server(self) -> bool:
        if self.sync_server is not None:
            return True
        try:
            self.sync_server = AudoraSyncServer(library_dirs=self._download_dirs(), port=DEFAULT_SYNC_PORT)
            self.sync_server.start()
            self.sync_error = ""
            self._sync_activity.append(f"[{human_time()}] Local sync started on {self.sync_server.base_url}")
            return True
        except Exception as exc:  # pylint: disable=broad-except
            self.sync_server = None
            self.sync_error = f"Could not start local sync: {exc}"
            self._sync_activity.append(f"[{human_time()}] {self.sync_error}")
            return False

    def _update_sync_dirs(self) -> None:
        if self.sync_server is not None:
            self.sync_server.state.library_dirs = self._download_dirs()

    def _enqueue_log(self, text: str) -> None:
        clean = str(text or "")
        with self._lock:
            self._logs.append(clean)
            self._logs = self._logs[-60:]
        self._event_queue.put({"type": "log", "text": clean})

    def _progress_to_dict(self, progress: DownloadProgress) -> dict:
        data = asdict(progress)
        data["percent"] = round(float(data.get("percent") or 0), 1)
        data["speed"] = _format_bytes(data.get("speed")) + "/s" if data.get("speed") else ""
        data["eta"] = _format_eta(data.get("eta"))
        downloaded = int(data.get("downloaded") or 0)
        total = int(data.get("total") or 0)
        data["downloadedText"] = f"{_format_bytes(downloaded)} of {_format_bytes(total)}" if total else _format_bytes(downloaded) if downloaded else ""
        data["mode"] = "Audio" if self.settings.get("audio_quality") else "Download"
        return data

    def _enqueue_progress(self, progress: DownloadProgress) -> None:
        item = self._progress_to_dict(progress)
        with self._lock:
            job_id = item.get("job_id")
            if job_id:
                current = self._jobs.get(job_id, {})
                current.update(item)
                self._jobs[job_id] = current
            else:
                self._terminal_status = item
                if item.get("status") == "finished" and item.get("message") == "all_done":
                    for job in self._jobs.values():
                        if job.get("status") not in ("error", "stopped"):
                            job["status"] = "finished"
                            job["percent"] = 100
        self._event_queue.put({"type": "progress", "progress": item})

    def _history_entries(self, directory: str, include_folders: bool = True, limit: int = 80) -> List[dict]:
        entries: List[dict] = []
        try:
            with os.scandir(directory) as iterator:
                for entry in iterator:
                    if len(entries) >= limit:
                        break
                    try:
                        path = entry.path
                        if entry.is_file() and _is_media_file(entry.name):
                            stat = entry.stat()
                            thumb = _find_thumbnail(path)
                            ext = Path(entry.name).suffix.replace(".", "").upper()
                            media_info = probe_media(path)
                            has_video = media_info.has_video or ext in ("MP4", "WEBM", "MKV", "MOV", "AVI", "M4V")
                            entries.append(
                                {
                                    "type": "file",
                                    "mediaType": "Video" if has_video else "Audio",
                                    "name": entry.name,
                                    "title": entry.name,
                                    "path": path,
                                    "uri": self._media_url(path),
                                    "fileUri": _path_uri(path),
                                    "mtime": stat.st_mtime,
                                    "timestamp": _timestamp(stat.st_mtime),
                                    "size": _format_bytes(stat.st_size),
                                    "format": ext,
                                    "duration": format_media_time(media_info.duration) if media_info.duration else "",
                                    "count": 1,
                                    "thumbnailUri": _image_data_uri(thumb),
                                }
                            )
                        elif include_folders and entry.is_dir():
                            children = self._history_entries(path, include_folders=False, limit=200)
                            if not children:
                                continue
                            stat = entry.stat()
                            latest_child = max((child.get("mtime") or 0) for child in children)
                            size_bytes = sum(self._folder_size(path))
                            entries.append(
                                {
                                    "type": "folder",
                                    "name": entry.name,
                                    "title": entry.name,
                                    "path": path,
                                    "uri": _path_uri(path),
                                    "mtime": max(stat.st_mtime, latest_child),
                                    "timestamp": _timestamp(max(stat.st_mtime, latest_child)),
                                    "count": len(children),
                                    "size": _format_bytes(size_bytes),
                                    "children": children[:40],
                                }
                            )
                    except OSError:
                        continue
        except OSError:
            return []
        entries.sort(key=lambda item: item.get("mtime") or 0, reverse=True)
        return entries

    def _folder_size(self, directory: str) -> Iterable[int]:
        for root, _dirs, files in os.walk(directory):
            for filename in files:
                path = os.path.join(root, filename)
                try:
                    yield os.path.getsize(path)
                except OSError:
                    continue

    def _library_stats(self, directory: str, history: List[dict], library: List[dict]) -> dict:
        used_bytes = sum(self._folder_size(directory)) if directory and os.path.isdir(directory) else 0
        try:
            usage = shutil.disk_usage(directory) if directory and os.path.exists(directory) else shutil.disk_usage(default_download_dir())
            total_bytes = usage.total
        except OSError:
            total_bytes = 0
        percent = round((used_bytes / total_bytes) * 100) if total_bytes else 0
        playlists = [entry for entry in history if entry.get("type") == "folder"]
        videos = [entry for entry in library if entry.get("type") == "Video"]
        audio = [entry for entry in library if entry.get("type") != "Video"]
        return {
            "usedBytes": used_bytes,
            "totalBytes": total_bytes,
            "used": _format_bytes(used_bytes),
            "total": _format_bytes(total_bytes),
            "percent": max(0, min(100, percent)),
            "items": len(library) + len(playlists),
            "tracks": len(audio),
            "videos": len(videos),
            "playlists": len(playlists),
            "downloaded": len(library),
            "sessions": len(history),
        }

    def _library_entries(self, directory: str, limit: int = 80) -> List[dict]:
        rows: List[dict] = []
        if not directory or not os.path.isdir(directory):
            return rows
        for root, _dirs, files in os.walk(directory):
            for filename in files:
                if len(rows) >= limit:
                    return rows
                if not _is_media_file(filename):
                    continue
                path = os.path.join(root, filename)
                try:
                    stat = os.stat(path)
                except OSError:
                    continue
                ext = Path(filename).suffix.replace(".", "").upper()
                thumb = _find_thumbnail(path)
                media_info = probe_media(path)
                has_video = media_info.has_video or ext in ("MP4", "WEBM", "MKV", "MOV", "AVI", "M4V")
                rows.append(
                    {
                        "title": Path(filename).stem,
                        "artist": Path(root).name if Path(root).name else "Audora",
                        "type": "Video" if has_video else "Audio",
                        "duration": format_media_time(media_info.duration) if media_info.duration else "",
                        "status": "Downloaded",
                        "size": _format_bytes(stat.st_size),
                        "format": ext,
                        "path": path,
                        "uri": self._media_url(path),
                        "fileUri": _path_uri(path),
                        "timestamp": _timestamp(stat.st_mtime),
                        "mtime": stat.st_mtime,
                        "thumbnailUri": _image_data_uri(thumb),
                    }
                )
        return rows

    def _sync_state(self) -> dict:
        self._start_sync_server()
        server = self.sync_server
        if server is None:
            return {
                "online": False,
                "baseUrl": "",
                "host": "",
                "port": str(DEFAULT_SYNC_PORT),
                "error": self.sync_error,
                "devices": [],
                "trackCount": 0,
                "activity": [],
            }

        try:
            devices = [
                {
                    "id": device.id,
                    "name": device.name,
                    "platform": device.platform,
                    "lastSeen": _timestamp((device.last_seen_at or 0) / 1000),
                    "storage": "",
                }
                for device in server.database.list_paired_devices()
            ]
        except Exception:  # pylint: disable=broad-except
            devices = []
        try:
            track_count = server.database.count_tracks()
        except Exception:  # pylint: disable=broad-except
            track_count = 0
        try:
            activity = [
                {
                    "id": report.id,
                    "deviceName": report.device_name,
                    "trackTitle": report.track_title,
                    "status": report.status,
                    "timestamp": _timestamp(report.synced_at / 1000),
                }
                for report in server.database.list_recent_sync_reports(8)
            ]
        except Exception:  # pylint: disable=broad-except
            activity = []
        host = server.base_url.replace("http://", "").split(":")[0]
        return {
            "online": True,
            "baseUrl": server.base_url,
            "host": host,
            "port": str(server.port),
            "devices": devices,
            "trackCount": track_count,
            "activity": activity,
        }

    def _build_qr(self, payload: dict) -> str:
        import qrcode

        qr = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_H, border=2, box_size=8)
        qr.add_data(json.dumps(payload, separators=(",", ":")))
        qr.make(fit=True)
        image = qr.make_image(fill_color="#f8fafc", back_color="#070912").convert("RGBA")
        draw = ImageDraw.Draw(image)
        size = image.size[0]
        center = size // 2
        badge = max(44, size // 5)
        draw.rounded_rectangle(
            (center - badge // 2, center - badge // 2, center + badge // 2, center + badge // 2),
            radius=10,
            fill="#111827",
            outline="#ec4899",
            width=3,
        )
        draw.text((center - badge // 5, center - badge // 3), "A", fill="#ec4899")
        buf = io.BytesIO()
        image.save(buf, format="PNG")
        return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode("ascii")

    # pywebview API -----------------------------------------------------
    def get_state(self) -> dict:
        directory = (self.settings.get("download_dir") or "").strip() or default_download_dir()
        history = self._history_entries(directory)
        library = self._library_entries(directory)
        stats = self._library_stats(directory, history, library)
        with self._lock:
            downloads = {
                "jobs": list(self._jobs.values()),
                "terminal": self._terminal_status,
                "active": self.manager.has_active_jobs(),
            }
        return {
            "settings": self.settings,
            "history": history,
            "library": library,
            "stats": stats,
            "downloads": downloads,
            "sync": self._sync_state(),
            "qr": self._last_qr,
        }

    def poll_events(self) -> dict:
        changed = False
        events = []
        while True:
            try:
                events.append(self._event_queue.get_nowait())
                changed = True
            except queue.Empty:
                break
        with self._lock:
            jobs = list(self._jobs.values())
            logs = list(self._logs[-20:])
        return {
            "changed": changed,
            "events": events,
            "jobs": jobs,
            "logs": logs,
            "active": self.manager.has_active_jobs(),
            "terminal": self._terminal_status,
            "sync": self._sync_state(),
        }

    def start_download(self, payload: dict) -> dict:
        url = str((payload or {}).get("url") or "").strip()
        if not url:
            return {"ok": False, "error": "Paste a YouTube URL first."}
        if self.manager.has_active_jobs():
            return {"ok": False, "error": "A download is already running."}

        out_dir = str((payload or {}).get("outDir") or self.settings.get("download_dir") or default_download_dir()).strip()
        os.makedirs(out_dir, exist_ok=True)
        mode = str((payload or {}).get("mode") or "Audio")
        audio_quality = str((payload or {}).get("audioQuality") or self.settings.get("audio_quality") or "192 kbps")
        video_quality = str((payload or {}).get("videoQuality") or self.settings.get("video_quality") or "1080p")

        with self._lock:
            self._jobs.clear()
            self._terminal_status = {}

        def run() -> None:
            try:
                if mode.lower() == "video":
                    self.manager.start_video(url, out_dir, video_quality)
                else:
                    self.manager.start_audio(url, out_dir, AUDIO_QUALITIES.get(audio_quality, DEFAULT_BITRATE))
            except Exception as exc:  # pylint: disable=broad-except
                self._enqueue_log(f"[{human_time()}] Failed to start download: {exc}")
                self._enqueue_progress(DownloadProgress(status="error", message=str(exc)))

        thread = threading.Thread(target=run, name="AudoraWebDownload", daemon=True)
        thread.start()
        return {"ok": True, "message": "Download started."}

    def stop_download(self) -> dict:
        self.manager.stop_all()
        return {"ok": True, "message": "Download stopped."}

    def save_settings(self, settings: dict) -> dict:
        incoming = dict(self.settings)
        if isinstance(settings, dict):
            incoming.update(settings)
        self.settings = _save_settings(incoming)
        self.manager.set_max_workers(int(self.settings.get("parallel_downloads") or 3))
        self._update_sync_dirs()
        return {"ok": True, "message": "Settings saved.", "settings": self.settings}

    def choose_download_dir(self) -> dict:
        try:
            import webview

            window = webview.windows[0] if webview.windows else None
            if not window:
                return {"ok": False}
            result = window.create_file_dialog(webview.FOLDER_DIALOG, directory=self.settings.get("download_dir") or default_download_dir())
            if result:
                path = result[0] if isinstance(result, (list, tuple)) else result
                self.settings["download_dir"] = os.fspath(path)
                self.settings = _save_settings(self.settings)
                self._update_sync_dirs()
                return {"ok": True, "path": self.settings["download_dir"]}
        except Exception as exc:  # pylint: disable=broad-except
            return {"ok": False, "error": str(exc)}
        return {"ok": False}

    def open_path(self, payload: dict) -> dict:
        path = str((payload or {}).get("path") or self.settings.get("download_dir") or default_download_dir()).strip()
        if not path:
            return {"ok": False, "error": "No path selected."}
        target = path if os.path.isdir(path) else os.path.dirname(path)
        try:
            if sys.platform.startswith("win"):
                os.startfile(target)  # pylint: disable=no-member
            elif sys.platform == "darwin":
                os.system(f'open "{target}"')
            else:
                os.system(f'xdg-open "{target}"')
            return {"ok": True}
        except Exception as exc:  # pylint: disable=broad-except
            return {"ok": False, "error": str(exc)}

    def open_media(self, payload: dict) -> dict:
        path = str((payload or {}).get("path") or "").strip()
        if not path or not os.path.exists(path):
            return {"ok": False}
        try:
            if sys.platform.startswith("win"):
                os.startfile(path)  # pylint: disable=no-member
            elif sys.platform == "darwin":
                os.system(f'open "{path}"')
            else:
                os.system(f'xdg-open "{path}"')
            return {"ok": True}
        except Exception as exc:  # pylint: disable=broad-except
            return {"ok": False, "error": str(exc)}

    def delete_path(self, payload: dict) -> dict:
        path = str((payload or {}).get("path") or "").strip()
        root = (self.settings.get("download_dir") or default_download_dir()).strip()
        if not path or not os.path.exists(path):
            return {"ok": False, "error": "The file no longer exists."}
        if not _safe_joined(root, path) or os.path.normcase(os.path.abspath(path)) == os.path.normcase(os.path.abspath(root)):
            return {"ok": False, "error": "Audora will only delete items inside the download folder."}
        try:
            if os.path.isdir(path):
                shutil.rmtree(path)
            else:
                for target in [path] + _sidecars_for_file(path):
                    if os.path.exists(target):
                        os.remove(target)
            return {"ok": True}
        except OSError as exc:
            return {"ok": False, "error": str(exc)}

    def generate_pairing(self) -> dict:
        if not self._start_sync_server() or self.sync_server is None:
            return {"ok": False, "error": self.sync_error or "Sync server is offline."}
        self._update_sync_dirs()
        payload = self.sync_server.create_pairing_session()
        self._last_pairing_payload = payload
        self._last_qr = self._build_qr(payload)
        self._sync_activity.append(f"[{human_time()}] Pairing QR generated")
        return {"ok": True, "qr": self._last_qr, "payload": payload, "sync": self._sync_state()}

    def scan_library(self) -> dict:
        if not self._start_sync_server() or self.sync_server is None:
            return {"ok": False, "message": self.sync_error or "Sync server is offline."}
        self._update_sync_dirs()

        def run() -> None:
            try:
                tracks = self.sync_server.scan_library() if self.sync_server else []
                self._sync_activity.append(f"[{human_time()}] Indexed {len(tracks)} tracks")
                self._event_queue.put({"type": "sync", "message": f"Indexed {len(tracks)} tracks"})
            except Exception as exc:  # pylint: disable=broad-except
                self._sync_activity.append(f"[{human_time()}] Library scan failed: {exc}")
                self._event_queue.put({"type": "sync", "message": f"Library scan failed: {exc}"})

        threading.Thread(target=run, name="AudoraLibraryScan", daemon=True).start()
        return {"ok": True, "message": "Library scan started."}

    def test_sync(self) -> dict:
        if not self._start_sync_server() or self.sync_server is None:
            return {"ok": False, "message": self.sync_error or "Sync server is offline."}
        try:
            with urlopen(f"{self.sync_server.base_url}/api/v1/health", timeout=3) as response:
                ok = 200 <= int(response.status) < 300
            return {"ok": ok, "message": "Local sync test passed." if ok else "Local sync returned an unexpected response."}
        except Exception as exc:  # pylint: disable=broad-except
            return {"ok": False, "message": f"Local sync test failed: {exc}"}

    def close(self) -> None:
        try:
            self.manager.shutdown()
        finally:
            if self.sync_server is not None:
                self.sync_server.close()
                self.sync_server = None


def run_app() -> None:
    try:
        import webview
    except ModuleNotFoundError as exc:
        raise RuntimeError("pywebview is required for the Audora desktop UI. Run: pip install -r requirements.txt") from exc

    api = AudoraWebApi()
    web_dir = Path(__file__).resolve().parent / "web"
    static_server, url = _start_static_server(web_dir, api)
    webview.create_window(
        "Audora",
        url,
        js_api=api,
        width=1440,
        height=900,
        min_size=(1080, 720),
        background_color="#05060a",
    )
    try:
        webview.start(debug=False)
    finally:
        static_server.shutdown()
        static_server.server_close()
        api.close()
