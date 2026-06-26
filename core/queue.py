# -*- coding: utf-8 -*-
"""Queue controller for resolving playlists and running downloads in parallel."""

from __future__ import annotations

import threading
import uuid
import os
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import dataclass
from typing import Any, Callable, Dict, Iterable, Iterator, List, Optional

import yt_dlp
from yt_dlp.utils import sanitize_filename

from .downloader import DownloadProgress, YTAudioDownloader
from .utils import available_js_runtimes, human_time, is_likely_playlist_url


ProgressCallback = Callable[[DownloadProgress], None]
LogCallback = Callable[[str], None]


@dataclass
class QueueEntry:
    """Represents a single resolved item from a playlist or individual URL."""

    url: str
    title: str = ""
    index: Optional[int] = None
    total: Optional[int] = None
    thumbnail_url: Optional[str] = None
    playlist_title: str = ""
    playlist_id: str = ""


def _extract_thumbnail(entry: Dict[str, Any]) -> Optional[str]:
    thumb = entry.get("thumbnail")
    if isinstance(thumb, str) and thumb.startswith("http"):
        return thumb

    thumbs = entry.get("thumbnails")
    if isinstance(thumbs, list):
        for thumb_entry in thumbs:
            url = thumb_entry.get("url") if isinstance(thumb_entry, dict) else None
            if isinstance(url, str) and url.startswith("http"):
                return url
    return None


def _normalise_entry_url(entry: Dict[str, Any]) -> Optional[str]:
    url = entry.get("webpage_url") or entry.get("url")
    if isinstance(url, str) and url.startswith("http"):
        return url

    video_id = entry.get("id")
    if isinstance(video_id, str):
        return f"https://www.youtube.com/watch?v={video_id}"
    return None


def _metadata_opts() -> Dict[str, Any]:
    opts: Dict[str, Any] = {
        "skip_download": True,
        "extract_flat": True,
        "quiet": True,
        "lazy_playlist": True,
        "nocheckcertificate": True,
    }
    js_runtimes = available_js_runtimes()
    if js_runtimes:
        opts["js_runtimes"] = js_runtimes
        opts["remote_components"] = ["ejs:github"]
    return opts


def iter_entries(url: str, log: Optional[LogCallback] = None) -> Iterator[QueueEntry]:
    """Yield queue entries lazily without materialising full playlists."""

    opts = _metadata_opts()
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False, process=False)

            info_type = info.get("_type")
            if info_type in {"playlist", "multi_video"}:
                raw_entries: Iterable[Dict[str, Any]] = info.get("entries") or []
                playlist_title = info.get("title") or info.get("playlist_title") or ""
                playlist_id = info.get("id") or info.get("playlist_id") or ""
                total = info.get("playlist_count") or info.get("n_entries")

                if log:
                    label = playlist_title or playlist_id or "playlist"
                    if total:
                        log(f"[{human_time()}] 📜 Playlist detected: {label} ({total} items)")
                    else:
                        log(f"[{human_time()}] 📜 Playlist detected: {label}")

                for idx, entry in enumerate(raw_entries, start=1):
                    if not entry:
                        continue
                    resolved_url = _normalise_entry_url(entry)
                    if not resolved_url:
                        continue
                    yield QueueEntry(
                        url=resolved_url,
                        title=entry.get("title") or "",
                        index=idx,
                        total=int(total) if total else None,
                        thumbnail_url=_extract_thumbnail(entry),
                        playlist_title=playlist_title,
                        playlist_id=playlist_id,
                    )
            else:
                resolved_url = info.get("webpage_url") or info.get("original_url") or url
                title = info.get("title") or ""
                thumb = _extract_thumbnail(info)
                if log:
                    log(f"[{human_time()}] 🎧 Single item detected")
                yield QueueEntry(
                    url=resolved_url,
                    title=title,
                    index=1,
                    total=1,
                    thumbnail_url=thumb,
                )
    except Exception as exc:  # pylint: disable=broad-except
        if log:
            log(f"[{human_time()}] ⚠️ Failed to read link metadata: {exc}")
        if not is_likely_playlist_url(url):
            yield QueueEntry(url=url, title="", index=1, total=1)


def resolve_entries(url: str, log: Optional[LogCallback] = None) -> List[QueueEntry]:
    """Return all queue entries for callers that explicitly need a list."""

    entries = list(iter_entries(url, log=log))
    if log and entries and entries[0].total and entries[0].total > 1:
        log(f"[{human_time()}] 📜 Playlist resolved: {entries[0].total} items")
    return entries


def _escape_outtmpl_literal(value: str, fallback: str = "Playlist") -> str:
    safe = sanitize_filename(value or fallback, restricted=False) or fallback
    return safe.replace("%", "%%")


def _build_entry_outtmpl(entry: QueueEntry, out_dir: str) -> Optional[str]:
    """Build a per-entry template when queue metadata adds useful context."""

    is_playlist_item = bool(entry.playlist_title or entry.playlist_id or (entry.total and entry.total > 1))
    if not is_playlist_item:
        return None

    folder = _escape_outtmpl_literal(entry.playlist_title or entry.playlist_id)
    index_prefix = f"{entry.index:03d} - " if entry.index is not None else ""
    return os.path.join(out_dir, folder, f"{index_prefix}%(title)s.%(ext)s")


class DownloadManager:
    """Manage multiple download workers backed by a ThreadPoolExecutor."""

    def __init__(self, log_cb: LogCallback, progress_cb: ProgressCallback, max_workers: int = 3):
        self._log = log_cb
        self._progress = progress_cb
        try:
            initial_workers = int(max_workers)
        except (TypeError, ValueError):
            initial_workers = 1
        self._max_workers = max(1, initial_workers)
        self._executor = ThreadPoolExecutor(max_workers=self._max_workers, thread_name_prefix="yt-dl")
        self._lock = threading.Lock()
        self._stop_events: Dict[str, threading.Event] = {}
        self._futures: Dict[str, Future] = {}
        self._active_total: Optional[int] = None
        self._starting = False
        self._cancel_requested = False
        self._had_errors = False

    @property
    def max_workers(self) -> int:
        return self._max_workers

    # ------------------------------------------------------------------
    def has_active_jobs(self) -> bool:
        with self._lock:
            return self._starting or bool(self._futures)

    # ------------------------------------------------------------------
    def set_max_workers(self, max_workers: int) -> bool:
        try:
            desired = int(max_workers)
        except (TypeError, ValueError):
            return False

        desired = max(1, min(desired, 8))
        if desired == self._max_workers:
            return True

        with self._lock:
            if self._starting or self._futures:
                return False

        old_executor = self._executor
        self._executor = ThreadPoolExecutor(max_workers=desired, thread_name_prefix="yt-dl")
        self._max_workers = desired
        old_executor.shutdown(wait=False, cancel_futures=False)
        return True

    # ------------------------------------------------------------------
    def start_audio(self, url: str, out_dir: str, bitrate: str) -> None:
        self._start_jobs(url, out_dir, bitrate=bitrate, video_quality=None)

    def start_video(self, url: str, out_dir: str, quality: str) -> None:
        self._start_jobs(url, out_dir, bitrate=None, video_quality=quality)

    # ------------------------------------------------------------------
    def _start_jobs(self, url: str, out_dir: str, bitrate: Optional[str], video_quality: Optional[str]) -> None:
        with self._lock:
            if self._starting or self._futures:
                raise RuntimeError("Downloads are already running.")
            self._starting = True
            self._active_total = None
            self._cancel_requested = False
            self._had_errors = False

        started_cleanly = False
        entry_iter: Optional[Iterator[QueueEntry]] = None
        try:
            entry_iter = iter_entries(url, log=self._log)
            exhausted = False
            started_count = 0

            while True:
                with self._lock:
                    if self._cancel_requested:
                        break
                    active_count = len(self._futures)
                    max_workers = self._max_workers

                while not exhausted and active_count < max_workers:
                    try:
                        entry = next(entry_iter)
                    except StopIteration:
                        exhausted = True
                        break
                    except Exception as exc:  # pylint: disable=broad-except
                        self._log(f"[{human_time()}] 💥 Failed while reading playlist: {exc}")
                        with self._lock:
                            self._had_errors = True
                        exhausted = True
                        break

                    with self._lock:
                        if self._cancel_requested:
                            exhausted = True
                            break
                        if entry.total:
                            self._active_total = entry.total
                        else:
                            self._active_total = started_count + 1

                    self._submit_entry(entry, out_dir, bitrate, video_quality)
                    started_count += 1
                    active_count += 1

                with self._lock:
                    futures = list(self._futures.values())

                if exhausted and not futures:
                    break

                if not futures:
                    continue

                done, _pending = wait(futures, return_when=FIRST_COMPLETED)
                for future in done:
                    self._complete_future(future)

                if exhausted:
                    with self._lock:
                        if not self._futures:
                            break

            if not started_count:
                with self._lock:
                    had_cancel = self._cancel_requested
                    if not had_cancel and not self._had_errors:
                        self._had_errors = True
                if not had_cancel:
                    self._log(f"[{human_time()}] ❌ No downloadable items were found.")

            with self._lock:
                remaining_futures = list(self._futures.values())
            for future in remaining_futures:
                self._complete_future(future)

            started_cleanly = True
        finally:
            if entry_iter and hasattr(entry_iter, "close"):
                entry_iter.close()
            with self._lock:
                self._starting = False
                should_emit_terminal = started_cleanly and not self._futures

            if should_emit_terminal:
                self._emit_terminal_event()

    # ------------------------------------------------------------------
    def _submit_entry(
        self,
        entry: QueueEntry,
        out_dir: str,
        bitrate: Optional[str],
        video_quality: Optional[str],
    ) -> None:
        job_id = str(uuid.uuid4())
        stop_event = threading.Event()

        mode = "Video" if video_quality is not None else "Audio"
        self._emit_placeholder(job_id, entry, mode)

        future = self._executor.submit(
            self._run_worker,
            job_id,
            entry,
            out_dir,
            bitrate,
            video_quality,
            stop_event,
        )

        with self._lock:
            self._stop_events[job_id] = stop_event
            self._futures[job_id] = future

    # ------------------------------------------------------------------
    def _complete_future(self, future: Future) -> None:
        exception = future.exception()

        with self._lock:
            job_id = next((jid for jid, fut in self._futures.items() if fut is future), None)
            if job_id:
                self._futures.pop(job_id, None)
                self._stop_events.pop(job_id, None)

        if exception and not self._cancel_requested:
            if job_id:
                self._log(f"[{human_time()}] 💥 Worker {job_id[:8]} failed: {exception}")
            else:
                self._log(f"[{human_time()}] 💥 Worker failed: {exception}")
            with self._lock:
                self._had_errors = True

    # ------------------------------------------------------------------
    def _emit_placeholder(self, job_id: str, entry: QueueEntry, mode: str) -> None:
        placeholder = DownloadProgress(
            status="queued",
            message="queued",
            percent=0.0,
            title=entry.title or f"Item {entry.index}",
            item_index=entry.index,
            item_count=entry.total,
            job_id=job_id,
            thumbnail_url=entry.thumbnail_url,
            mode=mode,
        )
        self._progress(placeholder)

    # ------------------------------------------------------------------
    def _run_worker(
        self,
        job_id: str,
        entry: QueueEntry,
        out_dir: str,
        bitrate: Optional[str],
        video_quality: Optional[str],
        stop_event: threading.Event,
    ) -> None:
        def progress_cb(progress: DownloadProgress) -> None:
            if progress.job_id is None:
                progress.job_id = job_id
            if not progress.title and entry.title:
                progress.title = entry.title
            if progress.item_index is None:
                progress.item_index = entry.index
            if progress.item_count is None:
                progress.item_count = entry.total
            if progress.thumbnail_url is None:
                progress.thumbnail_url = entry.thumbnail_url
            if not progress.mode:
                progress.mode = "Video" if video_quality is not None else "Audio"
            if progress.status == "error":
                self._had_errors = True
            self._progress(progress)

        downloader = YTAudioDownloader(self._log, progress_cb, stop_event, job_id=job_id)
        outtmpl = _build_entry_outtmpl(entry, out_dir)
        if bitrate is not None:
            downloader.download(entry.url, out_dir, bitrate=bitrate, outtmpl=outtmpl)
        else:
            quality = video_quality or "720p"
            downloader.download_video(entry.url, out_dir, quality=quality, outtmpl=outtmpl)

    # ------------------------------------------------------------------
    def _on_future_done(self, job_id: str, future: Future) -> None:
        exception = future.exception()

        with self._lock:
            self._futures.pop(job_id, None)
            self._stop_events.pop(job_id, None)
            remaining = self._starting or bool(self._futures)

        if exception and not self._cancel_requested:
            self._log(f"[{human_time()}] 💥 Worker {job_id[:8]} failed: {exception}")

        if not remaining:
            self._emit_terminal_event()

    # ------------------------------------------------------------------
    def _emit_terminal_event(self) -> None:
        if self._cancel_requested:
            terminal = DownloadProgress(status="stopped", message="cancelled", job_id=None)
        elif self._had_errors:
            terminal = DownloadProgress(
                status="error",
                message="one_or_more_failed",
                item_count=self._active_total,
                job_id=None,
            )
        else:
            terminal = DownloadProgress(
                status="finished",
                message="all_done",
                percent=100.0,
                item_count=self._active_total,
                job_id=None,
            )
        self._progress(terminal)

    # ------------------------------------------------------------------
    def stop_all(self) -> None:
        with self._lock:
            self._cancel_requested = True
            events = list(self._stop_events.values())
        for event in events:
            event.set()

    # ------------------------------------------------------------------
    def wait_for_current_jobs(self) -> None:
        with self._lock:
            futures = list(self._futures.values())
        for future in futures:
            try:
                future.result()
            except Exception:  # pylint: disable=broad-except
                continue

    # ------------------------------------------------------------------
    def shutdown(self) -> None:
        self.stop_all()
        self.wait_for_current_jobs()
        self._executor.shutdown(wait=False, cancel_futures=True)
