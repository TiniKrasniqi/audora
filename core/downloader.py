
# -*- coding: utf-8 -*-
from dataclasses import dataclass
from typing import Callable, Optional
import os
import threading

import yt_dlp
from yt_dlp.utils import DownloadError, DownloadCancelled

from .utils import (
    available_js_runtimes,
    human_time,
    DEFAULT_BITRATE,
    is_likely_playlist_url,
    js_runtime_warning_message,
)


@dataclass
class DownloadProgress:
    status: str = ""         # 'downloading', 'finished', 'error', 'stopped', 'queued'
    percent: float = 0.0
    downloaded: int = 0
    total: int = 0
    speed: float = 0.0       # bytes/sec
    eta: Optional[int] = None
    message: str = ""
    title: str = ""
    item_index: Optional[int] = None
    item_count: Optional[int] = None
    job_id: Optional[str] = None
    thumbnail_url: Optional[str] = None
    mode: str = ""


class YTDLogger:
    """Forward yt-dlp log lines to UI."""
    def __init__(self, emit_cb: Callable[[str], None]):
        self.emit = emit_cb

    def debug(self, msg):
        m = str(msg)
        if m.strip():
            self.emit(f"[{human_time()}] {m}")

    def warning(self, msg):
        self.emit(f"[{human_time()}] ⚠️ {msg}")

    def error(self, msg):
        self.emit(f"[{human_time()}] ❌ {msg}")


class YTAudioDownloader:
    _js_runtime_warning_logged = False
    _js_runtime_warning_lock = threading.Lock()

    def __init__(self, log_cb: Callable[[str], None], progress_cb: Callable[[DownloadProgress], None], stop_event: threading.Event, job_id: Optional[str] = None):
        self.log = log_cb
        self.progress = progress_cb
        self.stop_event = stop_event
        self.job_id = job_id
        self._last_title: str = ""
        self._last_item_index: Optional[int] = None
        self._last_item_count: Optional[int] = None
        self._mode = "audio"

    def _progress_hook(self, d):
        if self.stop_event.is_set():
            raise DownloadCancelled("User requested stop.")

        status = d.get("status")
        info = d.get("info_dict") or {}
        title = info.get("track") or info.get("title") or info.get("alt_title") or info.get("id") or ""
        playlist_index = info.get("playlist_index")
        playlist_count = (
            info.get("playlist_count")
            or info.get("n_entries")
            or d.get("playlist_count")
            or d.get("n_entries")
        )

        if status == "downloading":
            speed = d.get("speed") or 0.0
            eta = d.get("eta")
            total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
            downloaded = d.get("downloaded_bytes") or 0
            pct = (downloaded / total * 100.0) if total else 0.0

            progress = DownloadProgress(
                status="downloading",
                percent=pct,
                downloaded=int(downloaded),
                total=int(total),
                speed=float(speed),
                eta=eta,
                message="downloading",
                title=title,
                item_index=int(playlist_index) if playlist_index is not None else None,
                item_count=int(playlist_count) if playlist_count else None,
                job_id=self.job_id,
                mode="Video" if self._mode == "video" else "Audio",
            )
            self._last_title = progress.title
            self._last_item_index = progress.item_index
            self._last_item_count = progress.item_count
            self.progress(progress)

        elif status == "finished":
            if self._mode == "video":
                message = "processing"
                self.log(f"[{human_time()}] ✅ Downloaded media stream; processing…")
            else:
                message = "postprocessing"
                self.log(f"[{human_time()}] ✅ Downloaded; converting…")
            progress = DownloadProgress(
                status="finished",
                message=message,
                percent=100.0,
                title=title,
                item_index=int(playlist_index) if playlist_index is not None else None,
                item_count=int(playlist_count) if playlist_count else None,
                job_id=self.job_id,
                mode="Video" if self._mode == "video" else "Audio",
            )
            self._last_title = progress.title
            self._last_item_index = progress.item_index
            self._last_item_count = progress.item_count
            self.progress(progress)

    def _add_common_opts(self, opts):
        js_runtimes = available_js_runtimes()
        if js_runtimes:
            opts["js_runtimes"] = js_runtimes
            opts["remote_components"] = ["ejs:github"]
        return opts

    def _log_js_runtime_warning_once(self):
        if available_js_runtimes():
            return

        with self._js_runtime_warning_lock:
            if self.__class__._js_runtime_warning_logged:
                return
            self.__class__._js_runtime_warning_logged = True

        self.log(f"[{human_time()}] ⚠️ {js_runtime_warning_message()}")

    def _build_outtmpl(self, url: str, out_dir: str) -> str:
        # Auto-select template based on URL heuristics (no UI toggle needed)
        if is_likely_playlist_url(url):
            # Put items in a playlist folder; fall back to playlist_id if title missing
            return os.path.join(out_dir, "%(playlist_title,playlist_id)s", "%(playlist_index)03d - %(title)s.%(ext)s")
        else:
            # Single: flat filename in chosen folder
            return os.path.join(out_dir, "%(title)s.%(ext)s")

    def build_opts(self, url: str, out_dir: str, bitrate: str = DEFAULT_BITRATE, outtmpl: Optional[str] = None):
        outtmpl = outtmpl or self._build_outtmpl(url, out_dir)
        opts = {
            "outtmpl": outtmpl,
            "format": "bestaudio/best",
            "writethumbnail": True,
            "postprocessors": [
                {"key": "FFmpegExtractAudio", "preferredcodec": "mp3", "preferredquality": bitrate},
                {"key": "FFmpegMetadata", "add_metadata": True, "add_chapters": True},
                {"key": "EmbedThumbnail", "already_have_thumbnail": True},
            ],
            # Do not ignore errors so they surface in the UI instead of reporting
            # a successful download despite failures.
            "retries": 3,
            "continuedl": True,
            "noprogress": True,
            "concurrent_fragment_downloads": 4,
            "windowsfilenames": True,
            "restrictfilenames": False,
            "lazy_playlist": True,
            "logger": YTDLogger(self.log),
            "progress_hooks": [self._progress_hook],
        }
        return self._add_common_opts(opts)

    def download(self, url: str, out_dir: str, bitrate: str = DEFAULT_BITRATE, outtmpl: Optional[str] = None):
        self._mode = "audio"
        opts = self.build_opts(url, out_dir, bitrate, outtmpl=outtmpl)
        mode = "Auto"
        self.log(f"[{human_time()}] ▶ Starting ({mode}) → MP3 {bitrate} kbps")
        self.log(f"[{human_time()}] 📁 Output: {out_dir}")
        self._log_js_runtime_warning_once()

        try:
            self.progress(DownloadProgress(status="downloading", message="preparing", job_id=self.job_id, mode="Audio"))
            with yt_dlp.YoutubeDL(opts) as ydl:
                ydl.download([url])
            if not self.stop_event.is_set():
                self.log(f"[{human_time()}] 🎵 Finished successfully.")
                self.progress(DownloadProgress(
                    status="finished",
                    message="all_done",
                    percent=100.0,
                    title=self._last_title,
                    item_index=self._last_item_index,
                    item_count=self._last_item_count,
                    job_id=self.job_id,
                    mode="Audio",
                ))

        except DownloadCancelled as e:
            self.log(f"[{human_time()}] ⏹ Stopped: {e}")
            self.progress(DownloadProgress(status="stopped", message=str(e), job_id=self.job_id, mode="Audio"))

        except DownloadError as e:
            self.log(f"[{human_time()}] ❌ Download error: {e}")
            self.progress(DownloadProgress(status="error", message=str(e), job_id=self.job_id, mode="Audio"))

        except Exception as e:
            self.log(f"[{human_time()}] 💥 Unexpected error: {e}")
            self.progress(DownloadProgress(status="error", message=str(e), job_id=self.job_id, mode="Audio"))

    def download_video(self, url: str, out_dir: str, quality: str = "720p", outtmpl: Optional[str] = None):
        self._mode = "video"
        opts = self.build_video_opts(url, out_dir, quality, outtmpl=outtmpl)
        self.log(f"[{human_time()}] ▶ Starting (Video) → MP4 {quality}")
        self.log(f"[{human_time()}] 📁 Output: {out_dir}")
        self._log_js_runtime_warning_once()

        try:
            self.progress(DownloadProgress(status="downloading", message="preparing", job_id=self.job_id, mode="Video"))
            with yt_dlp.YoutubeDL(opts) as ydl:
                ydl.download([url])
            if not self.stop_event.is_set():
                self.log(f"[{human_time()}] 🎥 Finished successfully.")
                self.progress(DownloadProgress(
                    status="finished",
                    message="all_done",
                    percent=100.0,
                    title=self._last_title,
                    item_index=self._last_item_index,
                    item_count=self._last_item_count,
                    job_id=self.job_id,
                    mode="Video",
                ))
        except DownloadCancelled as e:
            self.log(f"[{human_time()}] ⏹ Stopped: {e}")
            self.progress(DownloadProgress(status="stopped", message=str(e), job_id=self.job_id, mode="Video"))
        except Exception as e:
            self.log(f"[{human_time()}] 💥 Video error: {e}")
            self.progress(DownloadProgress(status="error", message=str(e), job_id=self.job_id, mode="Video"))


    def build_video_opts(self, url: str, out_dir: str, quality: str = "720p", outtmpl: Optional[str] = None):
        """Build yt-dlp options for video downloads."""
        outtmpl = outtmpl or self._build_outtmpl(url, out_dir)
        # map quality text to resolution cap
        # map quality text to resolution cap
        height_map = {
            "480p": "480",
            "720p": "720",
            "1080p": "1080",
            "1440p": "1440",
            "2160p": "2160",
            "2160p (4K)": "2160",
        }
        height = height_map.get(quality, "720")

        ydl_opts = {
            "outtmpl": outtmpl,
            "format": (
                f"bestvideo[height<={height}][ext=mp4][vcodec^=avc1]+bestaudio[ext=m4a]/"
                f"bestvideo[height<={height}][ext=mp4][vcodec^=h264]+bestaudio[ext=m4a]/"
                f"best[height<={height}][ext=mp4][vcodec^=avc1]/"
                f"best[height<={height}][ext=mp4]"
            ),
            "merge_output_format": "mp4",
            "format_sort": ["vcodec:h264", "acodec:aac", "ext:mp4:m4a", "res"],
            # Surface errors to the caller so the UI can react appropriately.
            "retries": 10,
            "continuedl": True,
            "noprogress": True,
            "logger": YTDLogger(self.log),
            "progress_hooks": [self._progress_hook],
            "concurrent_fragment_downloads": 4,
            "windowsfilenames": True,
        }
        return self._add_common_opts(ydl_opts)

