# -*- coding: utf-8 -*-
from __future__ import annotations

import hashlib
import mimetypes
import os
from pathlib import Path
from typing import Iterable, List, Optional

from core.media import probe_media

from .db import SyncDatabase, TrackRecord


MEDIA_EXTENSIONS = {".mp3", ".m4a", ".wav", ".flac", ".mp4", ".webm"}
ARTWORK_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}


def is_supported_media(path: str | os.PathLike[str]) -> bool:
    return Path(path).suffix.lower() in MEDIA_EXTENSIONS


def build_track_id(path: str | os.PathLike[str], size: int, modified_ns: int) -> str:
    normalized_path = os.path.normcase(os.path.abspath(os.fspath(path)))
    digest = hashlib.sha256(f"{normalized_path}\0{size}\0{modified_ns}".encode("utf-8")).hexdigest()
    return f"track_{digest[:24]}"


def build_mvp_hash(path: str | os.PathLike[str], size: int, modified_ns: int) -> str:
    normalized_path = os.path.normcase(os.path.abspath(os.fspath(path)))
    return hashlib.sha256(f"{normalized_path}\0{size}\0{modified_ns}".encode("utf-8")).hexdigest()


def _split_artist_title(stem: str) -> tuple[str, str]:
    for separator in (" - ", " – ", " — "):
        if separator in stem:
            artist, title = stem.split(separator, 1)
            return artist.strip(), title.strip() or stem.strip()
    return "", stem.strip()


def _find_artwork(path: Path) -> str:
    for extension in ARTWORK_EXTENSIONS:
        candidate = path.with_suffix(extension)
        if candidate.is_file():
            return str(candidate)
    return ""


def _guess_mime_type(path: Path) -> str:
    guessed, _ = mimetypes.guess_type(str(path))
    if guessed:
        return guessed
    fallbacks = {
        ".mp3": "audio/mpeg",
        ".m4a": "audio/mp4",
        ".wav": "audio/wav",
        ".flac": "audio/flac",
        ".mp4": "video/mp4",
        ".webm": "video/webm",
    }
    return fallbacks.get(path.suffix.lower(), "application/octet-stream")


class LibraryScanner:
    def __init__(self, database: SyncDatabase):
        self.database = database

    def scan(self, folders: Iterable[str | os.PathLike[str]]) -> List[TrackRecord]:
        indexed = []
        seen_paths = set()

        for folder in folders:
            root = Path(folder).expanduser()
            if not root.is_dir():
                continue

            for path in sorted(root.rglob("*")):
                if not path.is_file() or not is_supported_media(path):
                    continue

                try:
                    resolved = path.resolve()
                    if resolved in seen_paths:
                        continue
                    seen_paths.add(resolved)
                    track = self.track_from_file(resolved)
                except OSError:
                    continue

                self.database.upsert_track(track)
                indexed.append(track)

        return indexed

    def track_from_file(self, path: Path) -> TrackRecord:
        stat = path.stat()
        artist, title = _split_artist_title(path.stem)
        media_info = probe_media(str(path))
        modified_ms = int(stat.st_mtime * 1000)

        return TrackRecord(
            id=build_track_id(path, stat.st_size, stat.st_mtime_ns),
            title=title or path.stem,
            artist=artist,
            album="",
            duration=int(media_info.duration or 0),
            mime_type=_guess_mime_type(path),
            extension=path.suffix.lower().lstrip("."),
            file_path=str(path),
            artwork_path=_find_artwork(path),
            size=int(stat.st_size),
            hash=build_mvp_hash(path, stat.st_size, stat.st_mtime_ns),
            created_at=modified_ms,
            updated_at=modified_ms,
        )
