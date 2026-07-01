import os
import tempfile
import threading
import unittest
from unittest import mock

from core.downloader import DownloadProgress
from core.utils import default_download_dir
from ui.web_app import AudoraWebApi, _clean_settings


def _api_for_directory(directory):
    api = object.__new__(AudoraWebApi)
    api.settings = {"download_dir": directory}
    api._media_base_url = ""
    api._lock = threading.RLock()
    api._library_dirty = False
    api._library_refreshing = False
    api._library_snapshot = {
        "directory": directory,
        "history": [],
        "library": [],
        "stats": {},
        "updatedAt": 0.0,
        "version": 0,
    }
    return api


class WebAppLibrarySnapshotTests(unittest.TestCase):
    def test_library_snapshot_counts_more_than_first_page_and_keeps_playlist_children(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            for index in range(82):
                with open(os.path.join(temp_dir, f"Track {index:03d}.mp3"), "wb") as media_file:
                    media_file.write(b"audio")

            playlist_dir = os.path.join(temp_dir, "Playlist")
            os.makedirs(playlist_dir)
            for index in range(45):
                with open(os.path.join(playlist_dir, f"Playlist Track {index:03d}.mp3"), "wb") as media_file:
                    media_file.write(b"audio")

            api = _api_for_directory(temp_dir)
            with mock.patch("core.media.probe_media", side_effect=AssertionError("Library listing should not probe media")):
                snapshot = api._build_library_snapshot(temp_dir)

            playlist = next(entry for entry in snapshot["history"] if entry["type"] == "folder")
            self.assertEqual(len(playlist["children"]), 45)
            self.assertEqual(snapshot["stats"]["sessions"], 83)
            self.assertEqual(snapshot["stats"]["downloaded"], 127)
            self.assertEqual(snapshot["stats"]["playlists"], 1)

    def test_delete_path_removes_media_sidecars_and_snapshot_entries(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = os.path.join(temp_dir, "Song.mp3")
            sidecar = os.path.join(temp_dir, "Song.jpg")
            with open(path, "wb") as media_file:
                media_file.write(b"audio")
            with open(sidecar, "wb") as image_file:
                image_file.write(b"image")

            api = _api_for_directory(temp_dir)
            api._library_snapshot["history"] = [{"type": "file", "path": path, "children": []}]
            api._library_snapshot["library"] = [{"path": path}]

            result = api.delete_path({"path": path})

            self.assertTrue(result["ok"])
            self.assertFalse(os.path.exists(path))
            self.assertFalse(os.path.exists(sidecar))
            self.assertEqual(api._library_snapshot["history"], [])
            self.assertEqual(api._library_snapshot["library"], [])

    def test_delete_path_treats_missing_safe_file_as_deleted(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = os.path.join(temp_dir, "Missing.mp3")
            api = _api_for_directory(temp_dir)
            api._library_snapshot["history"] = [{"type": "file", "path": path, "children": []}]
            api._library_snapshot["library"] = [{"path": path}]

            result = api.delete_path({"path": path})

            self.assertTrue(result["ok"])
            self.assertTrue(result["missing"])
            self.assertEqual(api._library_snapshot["history"], [])
            self.assertEqual(api._library_snapshot["library"], [])

    def test_progress_payload_preserves_video_mode(self):
        api = _api_for_directory("C:/music")
        payload = api._progress_to_dict(DownloadProgress(status="downloading", mode="Video"))

        self.assertEqual(payload["mode"], "Video")


class DefaultDownloadDirTests(unittest.TestCase):
    def test_new_default_download_dir_uses_audora(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            with mock.patch("core.utils.os.path.expanduser", return_value=temp_dir):
                path = default_download_dir()

            self.assertEqual(path, os.path.join(temp_dir, "Music", "Audora"))

    def test_existing_legacy_download_dir_is_preserved(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            legacy = os.path.join(temp_dir, "Music", "YouTubeDownloader")
            os.makedirs(legacy)
            with open(os.path.join(legacy, "old.mp3"), "wb") as media_file:
                media_file.write(b"audio")

            with mock.patch("core.utils.os.path.expanduser", return_value=temp_dir):
                path = default_download_dir()

            self.assertEqual(path, legacy)

    def test_legacy_2160p_setting_is_normalized(self):
        settings = _clean_settings({"video_quality": "2160p (4K)"})

        self.assertEqual(settings["video_quality"], "2160p")


if __name__ == "__main__":
    unittest.main()
