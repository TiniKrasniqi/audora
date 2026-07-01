import os
import shutil
import subprocess
import tempfile
import threading
import time
import unittest
from unittest import mock
from pathlib import Path

from core.downloader import YTAudioDownloader
from core.downloader import DownloadProgress
from core.media import VLC_RUNTIME_SHA256, VLC_RUNTIME_URL, format_media_time, probe_media
from core.queue import DownloadManager, QueueEntry, _build_entry_outtmpl
from core.utils import configure_ffmpeg_runtime


class DownloaderOptionsTests(unittest.TestCase):
    def _downloader(self):
        return YTAudioDownloader(lambda _msg: None, lambda _prog: None, threading.Event())

    def test_audio_options_embed_metadata_and_thumbnail(self):
        opts = self._downloader().build_opts("https://youtu.be/example", "C:/downloads", "192")

        postprocessor_keys = [pp["key"] for pp in opts["postprocessors"]]
        self.assertTrue(opts["writethumbnail"])
        self.assertIn("FFmpegExtractAudio", postprocessor_keys)
        self.assertIn("FFmpegMetadata", postprocessor_keys)
        self.assertIn("EmbedThumbnail", postprocessor_keys)

        thumbnail_pp = next(pp for pp in opts["postprocessors"] if pp["key"] == "EmbedThumbnail")
        self.assertTrue(thumbnail_pp["already_have_thumbnail"])

    def test_available_js_runtime_enables_remote_ejs_component(self):
        with mock.patch(
            "core.downloader.available_js_runtimes",
            return_value={"node": {"path": "C:/Program Files/nodejs/node.exe"}},
        ):
            opts = self._downloader().build_opts("https://youtu.be/example", "C:/downloads", "192")

        self.assertEqual(opts["js_runtimes"], {"node": {"path": "C:/Program Files/nodejs/node.exe"}})
        self.assertEqual(opts["remote_components"], ["ejs:github"])

    def test_video_options_prefer_ios_playable_mp4_streams(self):
        opts = self._downloader().build_video_opts("https://youtu.be/example", "C:/downloads", "720p")

        self.assertIn("[ext=mp4][vcodec^=avc1]+bestaudio[ext=m4a]", opts["format"])
        self.assertEqual(opts["merge_output_format"], "mp4")
        self.assertIn("vcodec:h264", opts["format_sort"])
        self.assertIn("acodec:aac", opts["format_sort"])

    def test_playlist_entry_template_uses_queue_metadata(self):
        entry = QueueEntry(
            url="https://www.youtube.com/watch?v=abc",
            title="Song",
            index=3,
            total=12,
            playlist_title="Best: 100% Hits",
            playlist_id="PL123",
        )

        outtmpl = _build_entry_outtmpl(entry, "C:/music")

        self.assertIsNotNone(outtmpl)
        self.assertIn("003 - %(title)s.%(ext)s", outtmpl)
        self.assertNotIn("Best:", outtmpl)
        self.assertIn("100%% Hits", outtmpl)

    def test_single_entry_uses_downloader_default_template(self):
        entry = QueueEntry(url="https://www.youtube.com/watch?v=abc", title="Song", index=1, total=1)

        self.assertIsNone(_build_entry_outtmpl(entry, "C:/music"))


class DownloadManagerStateTests(unittest.TestCase):
    def test_resolving_phase_is_active_and_cancellable(self):
        log_lines = []
        progress_events = []
        resolver_entered = threading.Event()
        release_resolver = threading.Event()

        def fake_iter(_url, log=None):
            if log:
                log("resolving")
            resolver_entered.set()
            release_resolver.wait(timeout=5)
            if False:
                yield None

        manager = DownloadManager(log_lines.append, progress_events.append, max_workers=1)
        worker = threading.Thread(
            target=manager.start_audio,
            args=("https://www.youtube.com/playlist?list=PL123", "C:/music", "192"),
        )

        with mock.patch("core.queue.iter_entries", side_effect=fake_iter):
            worker.start()
            self.assertTrue(resolver_entered.wait(timeout=2))
            self.assertTrue(manager.has_active_jobs())

            manager.stop_all()
            release_resolver.set()
            worker.join(timeout=5)

        self.assertFalse(worker.is_alive())
        self.assertFalse(manager.has_active_jobs())
        self.assertEqual(progress_events[-1].status, "stopped")
        self.assertEqual(progress_events[-1].message, "cancelled")

    def test_playlist_scheduler_does_not_prefetch_past_parallel_limit(self):
        progress_events = []
        yielded_entries = []
        worker_started = threading.Event()
        release_workers = threading.Event()

        def fake_iter(_url, log=None):
            for idx in range(1, 7):
                yielded_entries.append(idx)
                yield QueueEntry(
                    url=f"https://www.youtube.com/watch?v={idx}",
                    title=f"Song {idx}",
                    index=idx,
                    total=6,
                    playlist_title="Playlist",
                    playlist_id="PL123",
                )

        def fake_run_worker(job_id, entry, out_dir, bitrate, video_quality, stop_event):
            worker_started.set()
            release_workers.wait(timeout=5)
            progress_events.append(
                DownloadProgress(
                    status="finished",
                    message="all_done",
                    percent=100,
                    title=entry.title,
                    item_index=entry.index,
                    item_count=entry.total,
                    job_id=job_id,
                )
            )

        manager = DownloadManager(lambda _msg: None, progress_events.append, max_workers=3)
        worker = threading.Thread(
            target=manager.start_audio,
            args=("https://www.youtube.com/playlist?list=PL123", "C:/music", "192"),
        )

        with (
            mock.patch("core.queue.iter_entries", side_effect=fake_iter),
            mock.patch.object(manager, "_run_worker", side_effect=fake_run_worker),
        ):
            worker.start()
            self.assertTrue(worker_started.wait(timeout=2))

            deadline = time.time() + 2
            while len(progress_events) < 3 and time.time() < deadline:
                time.sleep(0.01)

            self.assertEqual(yielded_entries, [1, 2, 3])
            self.assertTrue(manager.has_active_jobs())

            release_workers.set()
            worker.join(timeout=5)

        self.assertFalse(worker.is_alive())
        self.assertFalse(manager.has_active_jobs())
        self.assertEqual(yielded_entries, [1, 2, 3, 4, 5, 6])
        self.assertEqual(progress_events[-1].status, "finished")


class MediaHelperTests(unittest.TestCase):
    def test_format_media_time(self):
        self.assertEqual(format_media_time(0), "0:00")
        self.assertEqual(format_media_time(65), "1:05")
        self.assertEqual(format_media_time(3661), "1:01:01")
        self.assertEqual(format_media_time(None), "0:00")

    @unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg tools are required")
    def test_probe_media_detects_audio_and_video(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            output_path = os.path.join(temp_dir, "sample.mp4")
            subprocess.run(
                [
                    "ffmpeg",
                    "-y",
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-f",
                    "lavfi",
                    "-i",
                    "testsrc=size=160x90:rate=10:duration=1",
                    "-f",
                    "lavfi",
                    "-i",
                    "sine=frequency=440:duration=1",
                    "-shortest",
                    "-pix_fmt",
                    "yuv420p",
                    output_path,
                ],
                check=True,
            )

            info = probe_media(output_path)

            self.assertTrue(info.has_audio)
            self.assertTrue(info.has_video)
            self.assertEqual(info.width, 160)
            self.assertEqual(info.height, 90)
            self.assertGreater(info.duration, 0)

    @unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg tools are required")
    def test_probe_media_ignores_mp3_album_art_as_video(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            output_path = os.path.join(temp_dir, "cover_art_audio.mp3")
            subprocess.run(
                [
                    "ffmpeg",
                    "-y",
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-f",
                    "lavfi",
                    "-i",
                    "sine=frequency=440:duration=1",
                    "-f",
                    "lavfi",
                    "-i",
                    "color=c=white:size=32x32:duration=1",
                    "-map",
                    "0:a",
                    "-map",
                    "1:v",
                    "-c:a",
                    "libmp3lame",
                    "-c:v",
                    "mjpeg",
                    "-disposition:v",
                    "attached_pic",
                    output_path,
                ],
                check=True,
            )

            info = probe_media(output_path)

            self.assertTrue(info.has_audio)
            self.assertFalse(info.has_video)

    def test_vlc_runtime_download_metadata_targets_win64_zip(self):
        self.assertIn("/win64/", VLC_RUNTIME_URL)
        self.assertTrue(VLC_RUNTIME_URL.endswith(".zip"))
        self.assertEqual(len(VLC_RUNTIME_SHA256), 64)

    def test_configure_ffmpeg_runtime_uses_audora_runtime_dir(self):
        executable_suffix = ".exe" if os.sys.platform.startswith("win") else ""
        with tempfile.TemporaryDirectory() as temp_dir:
            bin_dir = Path(temp_dir) / "bin"
            bin_dir.mkdir()
            (bin_dir / f"ffmpeg{executable_suffix}").touch()
            (bin_dir / f"ffprobe{executable_suffix}").touch()

            with mock.patch.dict(os.environ, {"AUDORA_FFMPEG_DIR": temp_dir, "PATH": ""}):
                resolved = configure_ffmpeg_runtime()

                self.assertEqual(resolved, bin_dir.resolve())
                self.assertTrue(os.environ["PATH"].startswith(str(bin_dir.resolve())))


if __name__ == "__main__":
    unittest.main()
