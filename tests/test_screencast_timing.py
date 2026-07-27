import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from app.config import Settings
from app.pipeline.steps.screencast import (
    ScreencastError,
    build_args,
    build_screencast,
    plan_filters,
)


class PlanFiltersTests(unittest.TestCase):
    def setUp(self) -> None:
        # Narrow default band so tests exercise the clamp without huge inputs.
        self.settings = Settings(
            screencast_min_speed_factor=0.4,
            screencast_max_speed_factor=2.5,
            screencast_max_width=1920,
            screencast_fps=30,
        )

    def test_factor_inside_band_uses_plain_setpts(self) -> None:
        # 10s GIF, 15s narration -> factor 1.5, well inside [0.4, 2.5].
        filters, factor = plan_filters(10.0, 15.0, self.settings)

        self.assertAlmostEqual(factor, 1.5)
        self.assertEqual(filters[0], "setpts=PTS*1.500000")
        # No hold/trim needed — the scaled GIF already matches exactly.
        self.assertFalse(any(f.startswith("tpad") for f in filters))
        self.assertFalse(any(f.startswith("trim") for f in filters))

    def test_gif_far_too_short_stretches_to_cap_then_holds_last_frame(self) -> None:
        # 2s GIF, 40s narration -> wanted factor 20x, clamped to 2.5x -> 5s
        # covered, 35s still missing. Without the hold, the video would end 35s
        # before the narration does.
        filters, factor = plan_filters(2.0, 40.0, self.settings)

        self.assertAlmostEqual(factor, 2.5)
        self.assertEqual(filters[0], "setpts=PTS*2.500000")
        tpad = next(f for f in filters if f.startswith("tpad"))
        self.assertEqual(tpad, "tpad=stop_mode=clone:stop_duration=35.000000")
        self.assertFalse(any(f.startswith("trim") for f in filters))

    def test_gif_far_too_long_compresses_to_floor_then_trims(self) -> None:
        # 100s GIF, 20s narration -> wanted factor 0.2x, clamped to 0.4x -> the
        # scaled GIF is 250s, so it must be cut down to exactly 20s.
        filters, factor = plan_filters(100.0, 20.0, self.settings)

        self.assertAlmostEqual(factor, 0.4)
        self.assertEqual(filters[0], "setpts=PTS*0.400000")
        self.assertIn("trim=duration=20.000000", filters)
        self.assertIn("setpts=PTS-STARTPTS", filters)
        self.assertFalse(any(f.startswith("tpad") for f in filters))

    def test_zero_gif_duration_raises_instead_of_silently_defaulting(self) -> None:
        with self.assertRaises(ScreencastError):
            plan_filters(0.0, 20.0, self.settings)

    def test_negative_gif_duration_raises(self) -> None:
        with self.assertRaises(ScreencastError):
            plan_filters(-1.0, 20.0, self.settings)

    def test_zero_narration_duration_raises(self) -> None:
        with self.assertRaises(ScreencastError):
            plan_filters(5.0, 0.0, self.settings)

    def test_scale_and_fps_filters_always_appended(self) -> None:
        filters, _ = plan_filters(10.0, 15.0, self.settings)
        self.assertEqual(filters[-2], "scale='min(1920,iw)':-2:flags=lanczos")
        self.assertEqual(filters[-1], "fps=30")


class BuildArgsTests(unittest.TestCase):
    def test_args_disable_audio_and_force_seekable_h264(self) -> None:
        args = build_args(["setpts=PTS*1.000000"], Path("in.gif"), Path("out.mp4"))

        self.assertIn("-an", args)  # GIFs have no audio track to carry over
        self.assertIn("-c:v", args)
        self.assertEqual(args[args.index("-c:v") + 1], "libx264")
        self.assertIn("-pix_fmt", args)
        self.assertEqual(args[args.index("-pix_fmt") + 1], "yuv420p")
        self.assertIn("-movflags", args)
        self.assertEqual(args[args.index("-movflags") + 1], "+faststart")
        self.assertEqual(args[-1], "out.mp4")


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "requires ffmpeg/ffprobe")
class BuildScreencastIntegrationTests(unittest.IsolatedAsyncioTestCase):
    """Real ffmpeg, no mocks — this is the class of bug (path resolution, ffmpeg
    argv construction, concat demuxer wiring) that only shows up when the
    actual subprocess runs, not from testing the pure arithmetic alone.

    One GIF per scene now (not one shared GIF excerpted per scene) — each test
    GIF uses a visually distinct source pattern so a real content check (not
    just duration) is possible."""

    async def asyncSetUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmpdir = Path(self._tmp.name)
        self.gif_paths = [
            self.tmpdir / "step-000.gif",
            self.tmpdir / "step-001.gif",
            self.tmpdir / "step-002.gif",
        ]
        sources = [
            "testsrc=size=320x240:rate=10:duration=2",
            "smptebars=size=320x240:rate=10:duration=4",
            "testsrc2=size=320x240:rate=10:duration=3",
        ]
        for path, src in zip(self.gif_paths, sources):
            subprocess.run(
                ["ffmpeg", "-y", "-f", "lavfi", "-i", src, str(path)],
                check=True, capture_output=True,
            )
        self.settings = Settings()

    async def asyncTearDown(self) -> None:
        self._tmp.cleanup()

    async def test_concatenated_duration_matches_sum_of_scene_durations(self) -> None:
        timed_scenes = [
            {"id": "s1", "start": 0.0, "duration": 3.0},
            {"id": "s2", "start": 3.0, "duration": 5.0},
            {"id": "s3", "start": 8.0, "duration": 2.0},
        ]
        out_path = self.tmpdir / "screencast.mp4"

        duration = await build_screencast(self.settings, self.gif_paths, out_path, timed_scenes)

        self.assertTrue(out_path.is_file())
        # fps-quantization can round each of the 3 segments by up to ~1 frame;
        # tolerance scales with the segment count rather than being a fixed epsilon.
        tolerance = len(timed_scenes) * (1 / self.settings.screencast_fps) + 0.05
        self.assertAlmostEqual(duration, 10.0, delta=tolerance)

    async def test_relative_paths_work_despite_cwd_change(self) -> None:
        # Regression test for the path-resolution bug: ffmpeg previously ran
        # with cwd set to the output directory while receiving a RELATIVE
        # gif_path, so it looked for the file one directory too deep.
        import os

        cwd = os.getcwd()
        try:
            os.chdir(self.tmpdir)
            rel_gifs = [Path(p.name) for p in self.gif_paths]
            rel_out = Path("nested/screencast.mp4")
            timed_scenes = [
                {"id": "s1", "start": 0.0, "duration": 2.0},
                {"id": "s2", "start": 2.0, "duration": 2.0},
                {"id": "s3", "start": 4.0, "duration": 2.0},
            ]
            duration = await build_screencast(self.settings, rel_gifs, rel_out, timed_scenes)
            self.assertAlmostEqual(duration, 6.0, delta=0.5)
        finally:
            os.chdir(cwd)

    async def test_missing_gif_raises_clear_error(self) -> None:
        with self.assertRaises(ScreencastError):
            await build_screencast(
                self.settings, [self.tmpdir / "nope.gif"], self.tmpdir / "out.mp4",
                [{"id": "s1", "start": 0.0, "duration": 2.0}],
            )

    async def test_empty_scenes_raises(self) -> None:
        with self.assertRaises(ScreencastError):
            await build_screencast(self.settings, [], self.tmpdir / "out.mp4", [])

    async def test_gif_count_mismatch_raises(self) -> None:
        with self.assertRaises(ScreencastError):
            await build_screencast(
                self.settings, self.gif_paths[:2], self.tmpdir / "out.mp4",
                [
                    {"id": "s1", "start": 0.0, "duration": 2.0},
                    {"id": "s2", "start": 2.0, "duration": 2.0},
                    {"id": "s3", "start": 4.0, "duration": 2.0},
                ],
            )


if __name__ == "__main__":
    unittest.main()
