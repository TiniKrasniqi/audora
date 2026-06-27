# -*- coding: utf-8 -*-
from __future__ import annotations

import os
import sqlite3
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

from .security import generate_auth_token, hash_token, now_ms, random_id, token_matches


@dataclass
class TrackRecord:
    id: str
    title: str
    artist: str = ""
    album: str = ""
    duration: int = 0
    mime_type: str = "application/octet-stream"
    extension: str = ""
    file_path: str = ""
    artwork_path: str = ""
    size: int = 0
    hash: str = ""
    created_at: int = 0
    updated_at: int = 0

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "TrackRecord":
        return cls(
            id=row["id"],
            title=row["title"],
            artist=row["artist"] or "",
            album=row["album"] or "",
            duration=int(row["duration"] or 0),
            mime_type=row["mime_type"] or "application/octet-stream",
            extension=row["extension"] or "",
            file_path=row["file_path"] or "",
            artwork_path=row["artwork_path"] or "",
            size=int(row["size"] or 0),
            hash=row["hash"] or "",
            created_at=int(row["created_at"] or 0),
            updated_at=int(row["updated_at"] or 0),
        )

    def to_api_dict(self) -> Dict[str, object]:
        return {
            "id": self.id,
            "title": self.title,
            "artist": self.artist,
            "album": self.album,
            "duration": self.duration,
            "mimeType": self.mime_type,
            "extension": self.extension,
            "size": self.size,
            "hash": self.hash,
            "artworkUrl": f"/api/v1/tracks/{self.id}/artwork" if self.artwork_path else None,
            "downloadUrl": f"/api/v1/tracks/{self.id}/file",
            "createdAt": self.created_at,
            "updatedAt": self.updated_at,
        }


@dataclass
class PairedDevice:
    id: str
    name: str
    platform: str = ""
    created_at: int = 0
    last_seen_at: int = 0


@dataclass
class PairingResult:
    device_id: str
    auth_token: str
    server_id: str


@dataclass
class SyncReport:
    id: str
    device_id: str
    device_name: str
    track_id: str
    track_title: str
    status: str
    synced_at: int


class SyncDatabase:
    def __init__(self, db_path: str | os.PathLike[str]):
        self.db_path = str(db_path)
        if self.db_path != ":memory:":
            Path(self.db_path).expanduser().resolve().parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self.initialize()

    def close(self):
        with self._lock:
            self._conn.close()

    def initialize(self):
        with self._lock:
            self._conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS settings (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS tracks (
                    id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    artist TEXT,
                    album TEXT,
                    duration INTEGER,
                    mime_type TEXT,
                    extension TEXT,
                    file_path TEXT NOT NULL,
                    artwork_path TEXT,
                    size INTEGER NOT NULL,
                    hash TEXT NOT NULL,
                    created_at INTEGER NOT NULL,
                    updated_at INTEGER NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_tracks_updated_at
                    ON tracks(updated_at);

                CREATE TABLE IF NOT EXISTS playlists (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    created_at INTEGER NOT NULL,
                    updated_at INTEGER NOT NULL
                );

                CREATE TABLE IF NOT EXISTS playlist_tracks (
                    playlist_id TEXT NOT NULL,
                    track_id TEXT NOT NULL,
                    position INTEGER NOT NULL,
                    PRIMARY KEY (playlist_id, track_id)
                );

                CREATE TABLE IF NOT EXISTS pairing_sessions (
                    id TEXT PRIMARY KEY,
                    token_hash TEXT NOT NULL,
                    expires_at INTEGER NOT NULL,
                    created_at INTEGER NOT NULL,
                    used_at INTEGER
                );

                CREATE TABLE IF NOT EXISTS paired_devices (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    platform TEXT,
                    auth_token_hash TEXT NOT NULL,
                    created_at INTEGER NOT NULL,
                    last_seen_at INTEGER,
                    revoked_at INTEGER
                );

                CREATE INDEX IF NOT EXISTS idx_paired_devices_token
                    ON paired_devices(auth_token_hash);

                CREATE TABLE IF NOT EXISTS sync_history (
                    id TEXT PRIMARY KEY,
                    device_id TEXT NOT NULL,
                    track_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    synced_at INTEGER NOT NULL
                );
                """
            )
            self._conn.commit()

    def get_setting(self, key: str) -> Optional[str]:
        with self._lock:
            row = self._conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
            return str(row["value"]) if row else None

    def set_setting(self, key: str, value: str):
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO settings(key, value)
                VALUES(?, ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value
                """,
                (key, value),
            )
            self._conn.commit()

    def get_or_create_server_id(self) -> str:
        server_id = self.get_setting("server_id")
        if server_id:
            return server_id
        server_id = random_id("desktop")
        self.set_setting("server_id", server_id)
        return server_id

    def upsert_track(self, track: TrackRecord):
        current = now_ms()
        if not track.created_at:
            track.created_at = current
        if not track.updated_at:
            track.updated_at = current

        with self._lock:
            existing = self._conn.execute("SELECT created_at FROM tracks WHERE id = ?", (track.id,)).fetchone()
            created_at = int(existing["created_at"]) if existing else track.created_at
            self._conn.execute(
                """
                INSERT INTO tracks(
                    id, title, artist, album, duration, mime_type, extension,
                    file_path, artwork_path, size, hash, created_at, updated_at
                )
                VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    title = excluded.title,
                    artist = excluded.artist,
                    album = excluded.album,
                    duration = excluded.duration,
                    mime_type = excluded.mime_type,
                    extension = excluded.extension,
                    file_path = excluded.file_path,
                    artwork_path = excluded.artwork_path,
                    size = excluded.size,
                    hash = excluded.hash,
                    updated_at = excluded.updated_at
                """,
                (
                    track.id,
                    track.title,
                    track.artist,
                    track.album,
                    track.duration,
                    track.mime_type,
                    track.extension,
                    track.file_path,
                    track.artwork_path,
                    track.size,
                    track.hash,
                    created_at,
                    track.updated_at,
                ),
            )
            self._conn.commit()

    def get_track_by_id(self, track_id: str) -> Optional[TrackRecord]:
        with self._lock:
            row = self._conn.execute("SELECT * FROM tracks WHERE id = ?", (track_id,)).fetchone()
            return TrackRecord.from_row(row) if row else None

    def list_tracks(self) -> List[TrackRecord]:
        with self._lock:
            rows = self._conn.execute("SELECT * FROM tracks ORDER BY title COLLATE NOCASE, updated_at DESC").fetchall()
            return [TrackRecord.from_row(row) for row in rows]

    def prune_tracks_except(self, track_ids: set[str]):
        with self._lock:
            rows = self._conn.execute("SELECT id FROM tracks").fetchall()
            stale_ids = [row["id"] for row in rows if row["id"] not in track_ids]
            for index in range(0, len(stale_ids), 500):
                chunk = stale_ids[index : index + 500]
                placeholders = ",".join("?" for _ in chunk)
                self._conn.execute(f"DELETE FROM tracks WHERE id IN ({placeholders})", tuple(chunk))
                self._conn.execute(f"DELETE FROM playlist_tracks WHERE track_id IN ({placeholders})", tuple(chunk))
            self._conn.commit()

    def count_tracks(self) -> int:
        with self._lock:
            row = self._conn.execute("SELECT COUNT(*) AS count FROM tracks").fetchone()
            return int(row["count"] or 0)

    def create_pairing_session(self, pairing_token: str, expires_at: int) -> str:
        pairing_id = random_id("pair")
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO pairing_sessions(id, token_hash, expires_at, created_at, used_at)
                VALUES(?, ?, ?, ?, NULL)
                """,
                (pairing_id, hash_token(pairing_token), expires_at, now_ms()),
            )
            self._conn.commit()
        return pairing_id

    def complete_pairing(self, pairing_id: str, pairing_token: str, mobile_name: str, platform: str) -> Optional[PairingResult]:
        current = now_ms()
        with self._lock:
            row = self._conn.execute("SELECT * FROM pairing_sessions WHERE id = ?", (pairing_id,)).fetchone()
            if not row or row["used_at"] or int(row["expires_at"]) < current:
                return None
            if not token_matches(pairing_token, row["token_hash"]):
                return None

            device_id = random_id("mobile")
            auth_token = generate_auth_token()
            self._conn.execute("UPDATE pairing_sessions SET used_at = ? WHERE id = ?", (current, pairing_id))
            self._conn.execute(
                """
                INSERT INTO paired_devices(
                    id, name, platform, auth_token_hash, created_at, last_seen_at, revoked_at
                )
                VALUES(?, ?, ?, ?, ?, NULL, NULL)
                """,
                (
                    device_id,
                    (mobile_name or "Audora Mobile").strip()[:120],
                    (platform or "").strip()[:40],
                    hash_token(auth_token),
                    current,
                ),
            )
            self._conn.commit()
            return PairingResult(device_id=device_id, auth_token=auth_token, server_id=self.get_or_create_server_id())

    def validate_bearer_token(self, token: str) -> Optional[PairedDevice]:
        token_hash = hash_token(token)
        current = now_ms()
        with self._lock:
            row = self._conn.execute(
                """
                SELECT * FROM paired_devices
                WHERE auth_token_hash = ? AND revoked_at IS NULL
                """,
                (token_hash,),
            ).fetchone()
            if not row:
                return None
            self._conn.execute("UPDATE paired_devices SET last_seen_at = ? WHERE id = ?", (current, row["id"]))
            self._conn.commit()
            return PairedDevice(
                id=row["id"],
                name=row["name"],
                platform=row["platform"] or "",
                created_at=int(row["created_at"] or 0),
                last_seen_at=current,
            )

    def list_paired_devices(self) -> List[PairedDevice]:
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT * FROM paired_devices
                WHERE revoked_at IS NULL
                ORDER BY created_at DESC
                """
            ).fetchall()
            return [
                PairedDevice(
                    id=row["id"],
                    name=row["name"],
                    platform=row["platform"] or "",
                    created_at=int(row["created_at"] or 0),
                    last_seen_at=int(row["last_seen_at"] or 0),
                )
                for row in rows
            ]

    def revoke_device(self, device_id: str) -> bool:
        with self._lock:
            cursor = self._conn.execute(
                "UPDATE paired_devices SET revoked_at = ? WHERE id = ? AND revoked_at IS NULL",
                (now_ms(), device_id),
            )
            self._conn.commit()
            return cursor.rowcount > 0

    def record_sync_report(self, device_id: str, track_id: str, status: str, synced_at: Optional[int] = None) -> str:
        report_id = random_id("sync")
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO sync_history(id, device_id, track_id, status, synced_at)
                VALUES(?, ?, ?, ?, ?)
                """,
                (report_id, device_id, track_id, status, int(synced_at or now_ms())),
            )
            self._conn.commit()
        return report_id

    def list_recent_sync_reports(self, limit: int = 8) -> List[SyncReport]:
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT
                    sync_history.id,
                    sync_history.device_id,
                    COALESCE(paired_devices.name, '') AS device_name,
                    sync_history.track_id,
                    COALESCE(tracks.title, sync_history.track_id) AS track_title,
                    sync_history.status,
                    sync_history.synced_at
                FROM sync_history
                LEFT JOIN paired_devices ON paired_devices.id = sync_history.device_id
                LEFT JOIN tracks ON tracks.id = sync_history.track_id
                ORDER BY sync_history.synced_at DESC
                LIMIT ?
                """,
                (max(1, min(50, int(limit))),),
            ).fetchall()
            return [
                SyncReport(
                    id=row["id"],
                    device_id=row["device_id"],
                    device_name=row["device_name"] or "Audora Mobile",
                    track_id=row["track_id"],
                    track_title=row["track_title"] or row["track_id"],
                    status=row["status"],
                    synced_at=int(row["synced_at"] or 0),
                )
                for row in rows
            ]
