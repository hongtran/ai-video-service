"""Per-step GIF screencasts -> one seekable, per-scene-paced MP4.

Why transcode at all: HyperFrames renders deterministically by seeking a PAUSED
timeline and screenshotting each frame. A GIF in an <img> animates on the
browser's own wall clock, which the renderer never drives — it would render as
frame 0 frozen for the whole video. A <video> is framework-owned media: the
runtime seeks it, so every rendered frame is reproducible.

Why one GIF per scene: the user uploads one short screen-recording GIF per
narration step (matched to its step by filename — see app/documents/docx.py and
app/api/router.py), so scene i's screencast clip simply IS step i's own GIF —
no excerpting, no proportional-time guessing about which part of a shared
recording corresponds to which words. Each clip is then speed-matched to that
scene's own ALIGNMENT-derived duration (the whole reason this step runs after
AUTHORING+ALIGNMENT, not before them) and every scene's clip is concatenated in
order into the final screencast.

Why clamp the speed factor: unbounded scaling looks broken. A GIF stretched 6x
is a near-frozen crawl; one squeezed to a fifth of its length is an unreadable
blur. Outside the band we do what an editor would: play as fast/slow as is
still watchable, then hold the last frame (clip too short) or trim (clip too
long).
"""
import asyncio
import logging
import shutil
import tempfile
from pathlib import Path

from app.config import Settings
from app.pipeline.steps import subproc

logger = logging.getLogger(__name__)


class ScreencastError(Exception):
    pass


def plan_filters(
    source_duration: float,
    target_duration: float,
    settings: Settings,
) -> tuple[list[str], float]:
    """Decide the ffmpeg video filters that make a clip of `source_duration`
    last exactly `target_duration`. Returns (filters, applied_speed_factor).

    Pure arithmetic, no I/O — this is the part worth unit-testing, because each
    branch is a distinct visual failure that no automated render check catches.
    Used once per scene segment (each scene's GIF excerpt vs. that scene's own
    duration), not once globally.
    """
    if source_duration <= 0:
        raise ScreencastError(
            "a screencast source clip has no usable duration (single frame or "
            "malformed) — it cannot be animated over its scene"
        )
    if target_duration <= 0:
        raise ScreencastError(
            f"target duration is {target_duration}s — nothing to match the "
            "screencast segment against"
        )

    lo = settings.screencast_min_speed_factor
    hi = settings.screencast_max_speed_factor
    wanted = target_duration / source_duration
    factor = min(max(wanted, lo), hi)
    
    filters = [f"setpts=PTS*{factor:.6f}"]
    scaled = source_duration * factor

    if scaled < target_duration:
        # Excerpt is too short even at maximum stretch: freeze the final frame
        # for the remainder rather than letting the clip end early (which
        # would leave the tail of this scene's narration over a black frame).
        hold = target_duration - scaled
        filters.append(f"tpad=stop_mode=clone:stop_duration={hold:.6f}")
    elif scaled > target_duration:
        # Excerpt is too long even at maximum compression: cut it off. The
        # remainder is lost, which is honest — the alternative is narration
        # over a demo that has already moved on to a different step.
        filters.append(f"trim=duration={target_duration:.6f}")
        filters.append("setpts=PTS-STARTPTS")

    # Cap the width (screen recordings are often 2560px+ retina captures) and
    # force even dimensions — yuv420p H.264 requires them.
    filters.append(
        f"scale='min({settings.screencast_max_width},iw)':-2:flags=lanczos"
    )
    filters.append(f"fps={settings.screencast_fps}")
    return filters, factor


async def probe_duration(path: Path) -> float:
    """Read a media file's duration with ffprobe. Returns 0.0 when ffprobe
    cannot determine one, so the caller's own error message applies."""
    if shutil.which("ffprobe") is None:
        raise ScreencastError(
            "ffprobe is required to measure the uploaded GIF but was not found "
            "on PATH"
        )
    proc = await asyncio.create_subprocess_exec(
        "ffprobe", "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        str(path),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    if proc.returncode != 0:
        tail = stderr.decode("utf-8", "replace").strip()[-400:]
        raise ScreencastError(f"ffprobe failed on {path.name}: ...{tail}")
    try:
        return float(stdout.decode("utf-8", "replace").strip())
    except ValueError:
        return 0.0


def build_args(filters: list[str], in_path: Path, out_path: Path) -> list[str]:
    return [
        "-y",
        # GIFs carry no audio; -an keeps ffmpeg from inventing an empty track.
        "-i", str(in_path),
        "-an",
        "-vf", ",".join(filters),
        "-c:v", "libx264",
        "-preset", "veryfast",
        "-crf", "20",
        # Chrome will not decode H.264 without this pixel format, and a video
        # that does not decode renders as a blank panel with no error.
        "-pix_fmt", "yuv420p",
        # Lets the renderer seek without decoding from the start of the file.
        "-movflags", "+faststart",
        str(out_path),
    ]


async def _build_segment(
    settings: Settings,
    gif_path: Path,
    target_duration: float,
    out_path: Path,
    *,
    scene_id: str,
) -> None:
    """One scene's screencast clip: speed-match this step's own GIF (whole
    clip, no excerpting — it already covers exactly one scene) to
    `target_duration` (this scene's own aligned duration)."""
    gif_duration = await probe_duration(gif_path)
    filters, _ = plan_filters(gif_duration, target_duration, settings)

    try:
        returncode, _, stderr = await subproc.run(
            "ffmpeg",
            build_args(filters, gif_path, out_path),
            cwd=str(out_path.parent),
            timeout=settings.screencast_timeout_seconds,
        )
    except asyncio.TimeoutError:
        raise ScreencastError(
            f"screencast segment for scene '{scene_id}' timed out after "
            f"{settings.screencast_timeout_seconds}s"
        ) from None

    if returncode != 0:
        raise ScreencastError(
            f"screencast segment for scene '{scene_id}' failed: ffmpeg exited "
            f"{returncode}: ...{stderr.strip()[-500:]}"
        )
    if not out_path.is_file():
        raise ScreencastError(
            f"screencast segment for scene '{scene_id}' reported success but "
            "produced no file"
        )


async def _concat_segments(
    settings: Settings, segment_paths: list[Path], out_path: Path
) -> None:
    """Join the per-scene clips into one MP4, in order. Re-encodes (never
    `-c copy`) — same reasoning as app/pipeline/steps/tts.py's `_concat_mp3`:
    stream-copying concatenated clips leaves timestamp discontinuities at each
    seam, which would desync this video against captions timed off the same
    scene boundaries."""
    # concat.txt and cwd must be the segments' own directory (not out_path's) —
    # the concat demuxer's `file '<name>'` entries are relative to cwd, and the
    # segments live in the tmpdir build_screencast created, not next to the
    # final out_path.
    segments_dir = segment_paths[0].parent
    list_path = segments_dir / "concat.txt"
    list_path.write_text(
        "".join(f"file '{p.name}'\n" for p in segment_paths), encoding="utf-8"
    )
    try:
        returncode, _, stderr = await subproc.run(
            "ffmpeg",
            [
                "-y",
                "-f", "concat",
                "-safe", "0",
                "-i", str(list_path),
                "-c:v", "libx264",
                "-preset", "veryfast",
                "-crf", "20",
                "-pix_fmt", "yuv420p",
                "-movflags", "+faststart",
                str(out_path),
            ],
            cwd=str(segments_dir),
            timeout=settings.screencast_timeout_seconds,
        )
    except asyncio.TimeoutError:
        raise ScreencastError(
            f"screencast concat timed out after {settings.screencast_timeout_seconds}s"
        ) from None

    if returncode != 0:
        raise ScreencastError(
            f"screencast concat failed: ffmpeg exited {returncode}: "
            f"...{stderr.strip()[-500:]}"
        )
    if not out_path.is_file():
        raise ScreencastError("screencast concat reported success but produced no file")


async def build_screencast(
    settings: Settings,
    gif_paths: list[Path],
    out_path: Path,
    timed_scenes: list[dict],
) -> float:
    """Build `out_path` as one MP4 per `timed_scenes` entry — `gif_paths[i]` is
    scene i's own step GIF, played whole and speed-matched to that scene's own
    `start`/`duration` (from ALIGNMENT) — concatenated in order. Returns the
    resulting total duration.

    `len(gif_paths) == len(timed_scenes)` (one step GIF per scene, matched by
    the caller — see app/pipeline/orchestrator.py). `timed_scenes` must already
    carry real `start`/`duration` — i.e. this runs AFTER alignment, not before."""
    if shutil.which("ffmpeg") is None:
        raise ScreencastError(
            "ffmpeg is required to transcode the GIF screencast but was not "
            "found on PATH"
        )
    if not timed_scenes:
        raise ScreencastError("no scenes to build a screencast from")
    if len(gif_paths) != len(timed_scenes):
        raise ScreencastError(
            f"{len(gif_paths)} step GIF(s) but {len(timed_scenes)} scene(s) — "
            "each scene needs exactly one step GIF"
        )
    missing = [str(p) for p in gif_paths if not p.is_file()]
    if missing:
        raise ScreencastError(f"step GIF(s) not found: {', '.join(missing)}")

    # Resolved to absolute before use: subproc.run below runs ffmpeg with cwd
    # set to the segments' tmpdir, so a relative gif_path (e.g. the artifact
    # store's default "artifacts/<job_id>/...") would otherwise be
    # re-interpreted relative to THAT new cwd instead of the process cwd —
    # ffmpeg then looks for the file one directory too deep and fails with
    # "No such file or directory" even though the file exists.
    gif_paths = [p.resolve() for p in gif_paths]
    out_path = out_path.resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="screencast-seg-", dir=str(out_path.parent)) as tmp:
        tmpdir = Path(tmp)
        segment_paths: list[Path] = []
        for i, (scene, gif_path) in enumerate(zip(timed_scenes, gif_paths)):
            seg_path = tmpdir / f"seg-{i:03d}.mp4"
            scene_id = str(scene.get("id", i))
            await _build_segment(
                settings, gif_path, scene["duration"], seg_path, scene_id=scene_id,
            )
            segment_paths.append(seg_path)
        await _concat_segments(settings, segment_paths, out_path)

    actual = await probe_duration(out_path)
    logger.info(
        "screencast: %d scene segment(s) -> %.2fs MP4",
        len(timed_scenes), actual,
    )
    return actual
