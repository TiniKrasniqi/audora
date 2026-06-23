# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import mimetypes
import os
import re
import socket
import threading
from dataclasses import dataclass, field
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Dict, Iterable, Optional
from urllib.parse import unquote, urlparse

from .db import PairedDevice, SyncDatabase
from .scanner import LibraryScanner
from .security import generate_pairing_token, now_ms


DEFAULT_SYNC_PORT = 37124
PAIRING_TTL_MS = 2 * 60 * 1000
MAX_JSON_BODY_BYTES = 64 * 1024
VALID_ID_RE = re.compile(r"^[A-Za-z0-9_-]{3,128}$")
SYNC_STATUSES = {"downloaded", "failed", "deleted", "skipped"}


def default_sync_db_path() -> str:
    if os.name == "nt":
        base_dir = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA") or os.path.expanduser("~")
    elif sys_platform() == "darwin":
        base_dir = os.path.join(os.path.expanduser("~"), "Library", "Application Support")
    else:
        base_dir = os.environ.get("XDG_DATA_HOME") or os.path.join(os.path.expanduser("~"), ".local", "share")
    return str(Path(base_dir) / "Audora" / "sync.sqlite3")


def sys_platform() -> str:
    import sys

    return sys.platform


def get_lan_ip() -> str:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(("8.8.8.8", 80))
        return sock.getsockname()[0]
    except OSError:
        try:
            return socket.gethostbyname(socket.gethostname())
        except OSError:
            return "127.0.0.1"
    finally:
        sock.close()


class PairingRateLimiter:
    def __init__(self, max_attempts: int = 8, window_ms: int = PAIRING_TTL_MS):
        self.max_attempts = max_attempts
        self.window_ms = window_ms
        self._attempts: Dict[str, list[int]] = {}
        self._lock = threading.Lock()

    def allow(self, key: str) -> bool:
        current = now_ms()
        cutoff = current - self.window_ms
        with self._lock:
            attempts = [timestamp for timestamp in self._attempts.get(key, []) if timestamp >= cutoff]
            if len(attempts) >= self.max_attempts:
                self._attempts[key] = attempts
                return False
            attempts.append(current)
            self._attempts[key] = attempts
            return True


@dataclass
class SyncServerState:
    database: SyncDatabase
    scanner: LibraryScanner
    library_dirs: list[str]
    host: str = "0.0.0.0"
    port: int = DEFAULT_SYNC_PORT
    app_version: str = "1.0.0"
    device_name: str = field(default_factory=socket.gethostname)
    rescan_on_library: bool = True
    rate_limiter: PairingRateLimiter = field(default_factory=PairingRateLimiter)

    def base_url(self) -> str:
        host_for_url = self.host
        if host_for_url in ("", "0.0.0.0", "::"):
            host_for_url = get_lan_ip()
        return f"http://{host_for_url}:{self.port}"

    def scan_library(self):
        if self.rescan_on_library:
            self.scanner.scan(self.library_dirs)


def create_pairing_payload(state: SyncServerState) -> dict:
    token = generate_pairing_token()
    expires_at = now_ms() + PAIRING_TTL_MS
    pairing_id = state.database.create_pairing_session(token, expires_at)
    return {
        "type": "audora_pair",
        "pairingId": pairing_id,
        "pairingToken": token,
        "expiresAt": expires_at,
        "baseUrl": state.base_url(),
        "serverId": state.database.get_or_create_server_id(),
    }


def _clean_id(value: str) -> str:
    value = (value or "").strip()
    return value if VALID_ID_RE.match(value) else ""


def create_handler(state: SyncServerState):
    class AudoraSyncRequestHandler(BaseHTTPRequestHandler):
        server_version = "AudoraSync/1.0"

        def log_message(self, _format, *args):
            return

        def do_GET(self):
            parsed = urlparse(self.path)
            path = parsed.path.rstrip("/") or "/"

            if path == "/api/v1/health":
                self._handle_health()
                return

            track_route = self._parse_track_route(path)
            if track_route:
                track_id, resource = track_route
                if resource == "file":
                    self._handle_track_file(track_id)
                    return
                if resource == "artwork":
                    self._handle_track_artwork(track_id)
                    return

            if path == "/api/v1/library":
                self._handle_library()
                return

            self._send_error(HTTPStatus.NOT_FOUND, "NOT_FOUND", "Endpoint not found.")

        def do_POST(self):
            parsed = urlparse(self.path)
            path = parsed.path.rstrip("/") or "/"

            if path == "/api/v1/pair/start":
                self._handle_pair_start()
                return
            if path == "/api/v1/pair/complete":
                self._handle_pair_complete()
                return
            if path == "/api/v1/sync/report":
                self._handle_sync_report()
                return

            self._send_error(HTTPStatus.NOT_FOUND, "NOT_FOUND", "Endpoint not found.")

        def _parse_track_route(self, path: str) -> Optional[tuple[str, str]]:
            segments = [unquote(segment) for segment in path.strip("/").split("/")]
            if len(segments) != 5 or segments[:3] != ["api", "v1", "tracks"]:
                return None
            track_id = _clean_id(segments[3])
            resource = segments[4]
            if not track_id or resource not in {"file", "artwork"}:
                return None
            return track_id, resource

        def _read_json(self) -> Optional[dict]:
            try:
                content_length = int(self.headers.get("Content-Length") or "0")
            except ValueError:
                self._send_error(HTTPStatus.BAD_REQUEST, "INVALID_REQUEST", "Invalid Content-Length header.")
                return None
            if content_length > MAX_JSON_BODY_BYTES:
                self._send_error(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, "REQUEST_TOO_LARGE", "JSON body is too large.")
                return None
            if content_length <= 0:
                return {}
            try:
                raw = self.rfile.read(content_length)
                payload = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                self._send_error(HTTPStatus.BAD_REQUEST, "INVALID_JSON", "Request body must be valid JSON.")
                return None
            if not isinstance(payload, dict):
                self._send_error(HTTPStatus.BAD_REQUEST, "INVALID_JSON", "Request body must be a JSON object.")
                return None
            return payload

        def _send_json(self, status: HTTPStatus, payload: dict, headers: Optional[dict[str, str]] = None):
            body = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
            self.send_response(status.value)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            for key, value in (headers or {}).items():
                self.send_header(key, value)
            self.end_headers()
            self.wfile.write(body)

        def _send_error(self, status: HTTPStatus, code: str, message: str):
            self._send_json(status, {"ok": False, "error": code, "message": message})

        def _require_auth(self) -> Optional[PairedDevice]:
            header = self.headers.get("Authorization") or ""
            scheme, _, token = header.partition(" ")
            if scheme.lower() != "bearer" or not token.strip():
                self._send_error(HTTPStatus.UNAUTHORIZED, "UNAUTHORIZED", "Missing bearer token.")
                return None
            device = state.database.validate_bearer_token(token.strip())
            if not device:
                self._send_error(HTTPStatus.UNAUTHORIZED, "UNAUTHORIZED", "Invalid or revoked bearer token.")
                return None
            return device

        def _handle_health(self):
            self._send_json(
                HTTPStatus.OK,
                {
                    "ok": True,
                    "app": "Audora Desktop",
                    "version": state.app_version,
                    "serverId": state.database.get_or_create_server_id(),
                    "deviceName": state.device_name,
                    "port": state.port,
                },
            )

        def _handle_pair_start(self):
            self._send_json(HTTPStatus.OK, create_pairing_payload(state))

        def _handle_pair_complete(self):
            client_key = self.client_address[0] if self.client_address else "unknown"
            if not state.rate_limiter.allow(client_key):
                self._send_error(HTTPStatus.TOO_MANY_REQUESTS, "RATE_LIMITED", "Too many pairing attempts.")
                return

            payload = self._read_json()
            if payload is None:
                return

            pairing_id = _clean_id(str(payload.get("pairingId") or ""))
            pairing_token = str(payload.get("pairingToken") or "").strip()
            if not pairing_id or not pairing_token:
                self._send_error(HTTPStatus.BAD_REQUEST, "INVALID_PAIRING", "Missing pairing id or token.")
                return

            result = state.database.complete_pairing(
                pairing_id=pairing_id,
                pairing_token=pairing_token,
                mobile_name=str(payload.get("mobileName") or "Audora Mobile"),
                platform=str(payload.get("platform") or ""),
            )
            if not result:
                self._send_error(HTTPStatus.UNAUTHORIZED, "PAIRING_EXPIRED", "Pairing code is invalid or expired.")
                return

            self._send_json(
                HTTPStatus.OK,
                {
                    "ok": True,
                    "deviceId": result.device_id,
                    "authToken": result.auth_token,
                    "serverId": result.server_id,
                },
            )

        def _handle_library(self):
            if not self._require_auth():
                return
            try:
                state.scan_library()
                tracks = [track.to_api_dict() for track in state.database.list_tracks()]
            except OSError:
                self._send_error(HTTPStatus.INTERNAL_SERVER_ERROR, "SCAN_FAILED", "Could not scan the local library.")
                return
            self._send_json(HTTPStatus.OK, {"tracks": tracks})

        def _handle_track_file(self, track_id: str):
            if not self._require_auth():
                return
            track = state.database.get_track_by_id(track_id)
            if not track:
                self._send_error(HTTPStatus.NOT_FOUND, "TRACK_NOT_FOUND", "Track was not found.")
                return
            self._serve_file(track.file_path, track.mime_type)

        def _handle_track_artwork(self, track_id: str):
            if not self._require_auth():
                return
            track = state.database.get_track_by_id(track_id)
            if not track or not track.artwork_path:
                self._send_error(HTTPStatus.NOT_FOUND, "ARTWORK_NOT_FOUND", "Artwork was not found.")
                return
            mime_type = mimetypes.guess_type(track.artwork_path)[0] or "application/octet-stream"
            self._serve_file(track.artwork_path, mime_type)

        def _handle_sync_report(self):
            device = self._require_auth()
            if not device:
                return
            payload = self._read_json()
            if payload is None:
                return

            device_id = str(payload.get("deviceId") or "").strip()
            track_id = _clean_id(str(payload.get("trackId") or ""))
            status = str(payload.get("status") or "").strip().lower()
            if device_id and device_id != device.id:
                self._send_error(HTTPStatus.FORBIDDEN, "DEVICE_MISMATCH", "Sync report device does not match token.")
                return
            if not track_id or not state.database.get_track_by_id(track_id):
                self._send_error(HTTPStatus.NOT_FOUND, "TRACK_NOT_FOUND", "Track was not found.")
                return
            if status not in SYNC_STATUSES:
                self._send_error(HTTPStatus.BAD_REQUEST, "INVALID_STATUS", "Sync status is not supported.")
                return

            try:
                synced_at = int(payload.get("syncedAt") or now_ms())
            except (TypeError, ValueError):
                synced_at = now_ms()
            report_id = state.database.record_sync_report(device.id, track_id, status, synced_at)
            self._send_json(HTTPStatus.OK, {"ok": True, "reportId": report_id})

        def _serve_file(self, file_path: str, mime_type: str):
            path = Path(file_path)
            if not path.is_file():
                self._send_error(HTTPStatus.NOT_FOUND, "FILE_NOT_FOUND", "File is no longer available.")
                return

            file_size = path.stat().st_size
            start, end, partial = self._parse_range(file_size)
            if start is None or end is None:
                return

            content_length = max(0, end - start + 1)
            self.send_response(HTTPStatus.PARTIAL_CONTENT.value if partial else HTTPStatus.OK.value)
            self.send_header("Content-Type", mime_type or "application/octet-stream")
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Content-Length", str(content_length))
            if partial:
                self.send_header("Content-Range", f"bytes {start}-{end}/{file_size}")
            self.end_headers()

            try:
                with path.open("rb") as media_file:
                    media_file.seek(start)
                    remaining = content_length
                    while remaining > 0:
                        chunk = media_file.read(min(64 * 1024, remaining))
                        if not chunk:
                            break
                        self.wfile.write(chunk)
                        remaining -= len(chunk)
            except (BrokenPipeError, ConnectionError, OSError):
                return

        def _parse_range(self, file_size: int) -> tuple[Optional[int], Optional[int], bool]:
            if file_size <= 0:
                return 0, -1, False

            range_header = self.headers.get("Range")
            if not range_header:
                return 0, file_size - 1, False

            match = re.match(r"^bytes=(\d*)-(\d*)$", range_header.strip())
            if not match:
                self._send_range_error(file_size)
                return None, None, False

            start_text, end_text = match.groups()
            try:
                if start_text == "":
                    suffix_length = int(end_text)
                    if suffix_length <= 0:
                        raise ValueError
                    start = max(file_size - suffix_length, 0)
                    end = file_size - 1
                else:
                    start = int(start_text)
                    end = int(end_text) if end_text else file_size - 1
            except ValueError:
                self._send_range_error(file_size)
                return None, None, False

            if start < 0 or end < start or start >= file_size:
                self._send_range_error(file_size)
                return None, None, False

            return start, min(end, file_size - 1), True

        def _send_range_error(self, file_size: int):
            self.send_response(HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE.value)
            self.send_header("Content-Range", f"bytes */{file_size}")
            self.send_header("Content-Length", "0")
            self.end_headers()

    return AudoraSyncRequestHandler


class AudoraSyncServer:
    def __init__(
        self,
        db_path: str | os.PathLike[str] | None = None,
        library_dirs: Optional[Iterable[str | os.PathLike[str]]] = None,
        host: str = "0.0.0.0",
        port: int = DEFAULT_SYNC_PORT,
        app_version: str = "1.0.0",
        device_name: Optional[str] = None,
        rescan_on_library: bool = True,
    ):
        self.database = SyncDatabase(db_path or default_sync_db_path())
        self.scanner = LibraryScanner(self.database)
        self.state = SyncServerState(
            database=self.database,
            scanner=self.scanner,
            library_dirs=[os.fspath(path) for path in (library_dirs or [])],
            host=host,
            port=port,
            app_version=app_version,
            device_name=device_name or socket.gethostname(),
            rescan_on_library=rescan_on_library,
        )
        self._httpd: Optional[ThreadingHTTPServer] = None
        self._thread: Optional[threading.Thread] = None

    @property
    def base_url(self) -> str:
        return self.state.base_url()

    @property
    def port(self) -> int:
        return self.state.port

    def start(self):
        if self._httpd:
            return
        handler = create_handler(self.state)
        self._httpd = ThreadingHTTPServer((self.state.host, self.state.port), handler)
        self.state.port = int(self._httpd.server_address[1])
        self._thread = threading.Thread(target=self._httpd.serve_forever, name="AudoraSyncServer", daemon=True)
        self._thread.start()

    def stop(self):
        if not self._httpd:
            return
        self._httpd.shutdown()
        self._httpd.server_close()
        if self._thread:
            self._thread.join(timeout=3)
        self._httpd = None
        self._thread = None

    def scan_library(self):
        return self.scanner.scan(self.state.library_dirs)

    def create_pairing_session(self) -> dict:
        return create_pairing_payload(self.state)

    def close(self):
        self.stop()
        self.database.close()
