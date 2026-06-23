import json
import os
import sqlite3
import tempfile
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from core.sync.db import SyncDatabase, TrackRecord
from core.sync.scanner import LibraryScanner
from core.sync.security import hash_token, now_ms
from core.sync.server import AudoraSyncServer


def _read_json(url, method="GET", token=None, payload=None):
    data = None
    headers = {}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = Request(url, data=data, headers=headers, method=method)
    with urlopen(request, timeout=5) as response:
        return json.loads(response.read().decode("utf-8"))


class SyncDatabaseTests(unittest.TestCase):
    def test_pairing_stores_hash_and_validates_bearer_token(self):
        database = SyncDatabase(":memory:")
        try:
            pairing_id = database.create_pairing_session("123456", now_ms() + 120_000)
            result = database.complete_pairing(pairing_id, "123456", "Altin Phone", "android")

            self.assertIsNotNone(result)
            self.assertTrue(result.auth_token.startswith("audora_"))
            self.assertIsNone(database.complete_pairing(pairing_id, "123456", "Other Phone", "ios"))

            device = database.validate_bearer_token(result.auth_token)
            self.assertIsNotNone(device)
            self.assertEqual(device.id, result.device_id)
            self.assertIsNone(database.validate_bearer_token("wrong-token"))

            rows = database._conn.execute("SELECT auth_token_hash FROM paired_devices").fetchall()
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["auth_token_hash"], hash_token(result.auth_token))
            self.assertNotEqual(rows[0]["auth_token_hash"], result.auth_token)
        finally:
            database.close()

    def test_track_count_and_recent_sync_reports(self):
        database = SyncDatabase(":memory:")
        try:
            database.upsert_track(
                TrackRecord(
                    id="track_001",
                    title="Song",
                    file_path="C:/music/song.mp3",
                    size=10,
                    hash="abc",
                )
            )
            pairing_id = database.create_pairing_session("654321", now_ms() + 120_000)
            result = database.complete_pairing(pairing_id, "654321", "Altin Phone", "android")

            self.assertEqual(database.count_tracks(), 1)
            database.record_sync_report(result.device_id, "track_001", "downloaded", now_ms())
            reports = database.list_recent_sync_reports()

            self.assertEqual(len(reports), 1)
            self.assertEqual(reports[0].device_name, "Altin Phone")
            self.assertEqual(reports[0].track_title, "Song")
            self.assertEqual(reports[0].status, "downloaded")
        finally:
            database.close()


class LibraryScannerTests(unittest.TestCase):
    def test_scanner_indexes_media_and_returns_api_safe_track_payload(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            track_path = os.path.join(temp_dir, "Artist - Song.mp3")
            artwork_path = os.path.join(temp_dir, "Artist - Song.jpg")
            with open(track_path, "wb") as media_file:
                media_file.write(b"audora-audio")
            with open(artwork_path, "wb") as artwork_file:
                artwork_file.write(b"art")

            database = SyncDatabase(":memory:")
            try:
                scanner = LibraryScanner(database)
                first_scan = scanner.scan([temp_dir])
                second_scan = scanner.scan([temp_dir])
                tracks = database.list_tracks()

                self.assertEqual(len(first_scan), 1)
                self.assertEqual(len(second_scan), 1)
                self.assertEqual(len(tracks), 1)
                self.assertEqual(tracks[0].artist, "Artist")
                self.assertEqual(tracks[0].title, "Song")

                payload = tracks[0].to_api_dict()
                self.assertNotIn("filePath", payload)
                self.assertNotIn(temp_dir, json.dumps(payload))
                self.assertEqual(payload["artworkUrl"], f"/api/v1/tracks/{tracks[0].id}/artwork")
                self.assertEqual(payload["downloadUrl"], f"/api/v1/tracks/{tracks[0].id}/file")
            finally:
                database.close()


class SyncServerTests(unittest.TestCase):
    def test_pair_library_and_range_download_flow(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            track_path = os.path.join(temp_dir, "Artist - Song.mp3")
            with open(track_path, "wb") as media_file:
                media_file.write(b"0123456789")

            server = AudoraSyncServer(
                db_path=os.path.join(temp_dir, "sync.sqlite3"),
                library_dirs=[temp_dir],
                host="127.0.0.1",
                port=0,
                device_name="Audora Test Desktop",
            )
            server.start()
            try:
                base_url = server.base_url
                health = _read_json(f"{base_url}/api/v1/health")
                self.assertTrue(health["ok"])
                self.assertEqual(health["app"], "Audora Desktop")
                self.assertEqual(health["deviceName"], "Audora Test Desktop")

                pair_start = _read_json(f"{base_url}/api/v1/pair/start", method="POST")
                self.assertEqual(pair_start["type"], "audora_pair")
                self.assertEqual(pair_start["baseUrl"], base_url)
                pair_complete = _read_json(
                    f"{base_url}/api/v1/pair/complete",
                    method="POST",
                    payload={
                        "pairingId": pair_start["pairingId"],
                        "pairingToken": pair_start["pairingToken"],
                        "mobileName": "Altin Phone",
                        "platform": "android",
                    },
                )
                token = pair_complete["authToken"]
                device_id = pair_complete["deviceId"]

                library = _read_json(f"{base_url}/api/v1/library", token=token)
                self.assertEqual(len(library["tracks"]), 1)
                track = library["tracks"][0]
                self.assertEqual(track["title"], "Song")
                self.assertNotIn(temp_dir, json.dumps(track))

                with self.assertRaises(HTTPError) as unauthorized:
                    urlopen(f"{base_url}{track['downloadUrl']}", timeout=5)
                self.assertEqual(unauthorized.exception.code, 401)

                request = Request(
                    f"{base_url}{track['downloadUrl']}",
                    headers={"Authorization": f"Bearer {token}", "Range": "bytes=2-5"},
                )
                with urlopen(request, timeout=5) as response:
                    self.assertEqual(response.status, 206)
                    self.assertEqual(response.headers["Content-Range"], "bytes 2-5/10")
                    self.assertEqual(response.read(), b"2345")

                report = _read_json(
                    f"{base_url}/api/v1/sync/report",
                    method="POST",
                    token=token,
                    payload={
                        "deviceId": device_id,
                        "trackId": track["id"],
                        "status": "downloaded",
                        "syncedAt": now_ms(),
                        "localPath": "mobile-private-path",
                    },
                )
                self.assertTrue(report["ok"])

                conn = sqlite3.connect(os.path.join(temp_dir, "sync.sqlite3"))
                try:
                    count = conn.execute("SELECT COUNT(*) FROM sync_history").fetchone()[0]
                finally:
                    conn.close()
                self.assertEqual(count, 1)
            finally:
                server.close()


if __name__ == "__main__":
    unittest.main()
