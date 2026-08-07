#!/usr/bin/env python3
"""Resume a failed/interrupted job from any pipeline step, without redoing the
steps before it — each earlier step's output is loaded straight from the
artifact directory instead of recomputed. Meant for the case where an
expensive/paid step (TTS, transcription, LLM authoring) already succeeded and
only a later step failed (e.g. the SCREENCAST ffmpeg bug, or a one-off render
failure) — re-running the whole pipeline via the API would pay for those
steps again.

The job record itself lives only in the server's in-memory JobRepository, so
this script never touches job status — it works directly off
artifacts/<job_id>/ and reconstructs the job's subject/orientation/language
from --flags (defaults match the user-guide subject). If the server process
is still running with this job's record in memory, the finished video is
immediately servable at GET /api/v1/videos/<job_id>/artifacts/video.mp4
(that route only checks the artifact exists, unlike /video which requires
status=COMPLETED). Otherwise read it straight from
artifacts/<job_id>/video.mp4.

Usage:
  ./myenv/bin/python scripts/resume_job.py <job_id> --from-step authoring
  ./myenv/bin/python scripts/resume_job.py <job_id> --from-step screencast
  ./myenv/bin/python scripts/resume_job.py <job_id> --from-step render
  ./myenv/bin/python scripts/resume_job.py <job_id> --from-step narration --query "How to invite a teammate"

Caveats:
- For subject=user-guide, SEGMENT is deterministic (one scene per uploaded
  step — see app/pipeline/steps/segment.py's build_scene_index_from_steps) and
  reads its per-step narration straight from the already-saved steps.json, not
  from an LLM call. For every other subject, SEGMENT is the usual LLM call and
  its video metadata (description/hashtags/tags) isn't persisted as its own
  artifact — resuming at "authoring" or later means it's blank in meta.json,
  harmless for the render, cosmetic for YouTube upload.
- SCREENCAST runs AFTER alignment (each scene's own step GIF is paced to that
  scene's own aligned duration), so --from-step screencast reuses the
  already-authored scenes.json and skips LLM authoring entirely — the cheap
  path for a screencast-only bug. Its step GIFs are read from
  source_gifs/step-*.gif (saved at upload time, matched by filename from the
  guide's own captions — see app/documents/docx.py).
- ALIGNMENT and COMPOSE are pure/local (no paid API call) and always run
  fresh once execution reaches them, rather than being individually
  resumable — there's nothing to load; align_scenes' output isn't persisted
  on its own, only inside data.json after compose.
- --from-step render / thumbnail is the one shortcut that skips straight to
  the end: it loads data.json as-is (which already contains the composed,
  timed scenes) rather than re-running alignment/compose/screencast at all.
"""
import argparse
import asyncio
import sys
from pathlib import Path

# Allow `python scripts/resume_job.py ...` from the repo root without needing
# `-m` or PYTHONPATH set — this script is meant to be run ad hoc, not imported.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.api.router import _title_from_script
from app.config import Settings
from app.llm.client import build_openai_client
from app.pipeline.orchestrator import RealVideoPipeline
from app.pipeline.steps import author, compose, images, layout_gate, narration, render, screencast as screencast_step, segment, tts, thumbnail, transcribe
from app.pipeline.steps.align import align_scenes
from app.pipeline.steps.scene_split import load_scene_schema
from app.pipeline.steps.segment import SceneIndex
from app.storage.artifacts import LocalArtifactStore
from app.storage.jobs import InMemoryJobRepository
from app.subjects import get_subject_config

# Steps with a real, individually-loadable artifact and (for most of them) a
# real API/LLM cost — these are what --from-step actually chooses between.
# "screencast" is NOT here: it now runs after alignment (it needs each scene's
# real start/duration), not right after transcription — see ALL_STEPS below.
PRODUCER_STEPS = [
    "narration", "segment", "tts", "transcription", "authoring", "image_gen",
]
# Full set of valid --from-step values, in real pipeline order. alignment and
# compose/layout_gate are always run fresh (see should_run() below) once
# execution reaches them; screencast sits between them and IS individually
# resumable (screencast.mp4 is a real persisted artifact) — e.g.
# --from-step screencast reuses the already-authored scenes.json and skips
# LLM authoring entirely, useful for a screencast-only bug.
ALL_STEPS = PRODUCER_STEPS + ["alignment", "screencast", "compose", "layout_gate", "render", "thumbnail"]


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("job_id")
    p.add_argument("--from-step", choices=ALL_STEPS, default="authoring")
    p.add_argument("--subject", default="user-guide")
    p.add_argument("--orientation", default="horizontal", choices=["vertical", "horizontal"])
    p.add_argument("--language", default="en")
    p.add_argument("--query", default=None, help="Required when --from-step=narration (topic mode).")
    p.add_argument("--title", default=None, help="Override the derived video title/topic.")
    return p.parse_args()


def should_run(step: str, from_step: str) -> bool:
    return ALL_STEPS.index(step) >= ALL_STEPS.index(from_step)


async def main() -> None:
    args = parse_args()
    job_id = args.job_id
    settings = Settings()
    client = build_openai_client(settings)
    subject_config = get_subject_config(args.subject, settings)
    artifacts = LocalArtifactStore(settings.artifacts_dir)

    job_dir = artifacts.path_for(job_id, "")
    if not job_dir.is_dir():
        raise SystemExit(f"no artifacts found at {job_dir} — check the job id")

    skip_to_render = args.from_step in ("render", "thumbnail")

    if skip_to_render:
        data = artifacts.load_json(job_id, "data.json")
        data_path = artifacts.path_for(job_id, "data.json")
        timed_scenes = data["scenes"]
        query = args.title or data.get("config", {}).get("topic") or job_id
        audio_path = artifacts.path_for(job_id, "narration.mp3")
        screencast_path = (
            artifacts.path_for(job_id, "screencast.mp4")
            if subject_config.media_source == "screencast" else None
        )
        print(f"[skip-to-render] loaded existing data.json ({len(timed_scenes)} scene(s))")
    else:
        if should_run("narration", args.from_step):
            if not args.query:
                raise SystemExit("--from-step narration requires --query")
            script = await narration.generate_script(
                client, settings, subject_config, args.query, args.orientation, args.language,
            )
            artifacts.save_text(job_id, "script.txt", script)
        else:
            script = artifacts.path_for(job_id, "script.txt").read_text("utf-8")
        print(f"[narration] script: {len(script)} chars")

        if should_run("segment", args.from_step):
            if subject_config.media_source == "screencast":
                steps_manifest = artifacts.load_json(job_id, "steps.json")
                narrations = [s["narration"] for s in steps_manifest]
                sentences, scenes_index = segment.build_scene_index_from_steps(
                    narrations, args.orientation
                )
                metadata = {}
            else:
                sentences = segment.build_sentence_index(script)
                scenes_index, metadata = await segment.segment_script(
                    client, settings, subject_config, sentences,
                    orientation=args.orientation, language=args.language,
                )
            segment.assert_three_way_equality(scenes_index, sentences, script)
            artifacts.save_json(job_id, "sentences.json", sentences)
            artifacts.save_json(job_id, "scenes_index.json", [s.to_dict() for s in scenes_index])
        else:
            sentences = artifacts.load_json(job_id, "sentences.json")
            raw_index = artifacts.load_json(job_id, "scenes_index.json")
            scenes_index = [SceneIndex(**d) for d in raw_index]
            # Not persisted anywhere on its own — see module docstring.
            metadata = {}
        print(f"[segment] {len(scenes_index)} scene(s)")

        if should_run("tts", args.from_step):
            audio = await tts.synthesize(client, settings, script, args.language, subject=args.subject)
            audio_path = artifacts.save_bytes(job_id, "narration.mp3", audio)
        else:
            audio_path = artifacts.path_for(job_id, "narration.mp3")
            if not audio_path.is_file():
                raise SystemExit(f"expected {audio_path} to already exist")
        print(f"[tts] audio: {audio_path}")

        if should_run("transcription", args.from_step):
            words, duration, transcript_text = await transcribe.transcribe_words(
                client, settings, audio_path, args.language
            )
            artifacts.save_json(job_id, "transcript.json", {"text": transcript_text, "words": words})
        else:
            saved = artifacts.load_json(job_id, "transcript.json")
            words, transcript_text = saved["words"], saved["text"]
            # transcript.json doesn't persist the scalar duration itself —
            # ffprobe the audio directly, same source of truth the SCREENCAST
            # step itself uses.
            duration = await screencast_step.probe_duration(audio_path)
        print(f"[transcription] {len(words)} word(s), duration {duration:.2f}s")

        sentences_by_index = {int(s["i"]): s["text"] for s in sentences}

        if should_run("authoring", args.from_step):
            scenes = await author.author_scenes(
                client, settings, subject_config, scenes_index, sentences_by_index, script,
                orientation=args.orientation, language=args.language,
            )
            artifacts.save_json(job_id, "scenes.json", scenes)
        else:
            scenes = artifacts.load_json(job_id, "scenes.json")
        print(f"[authoring] {len(scenes)} scene(s)")

        if should_run("image_gen", args.from_step):
            scenes = await images.resolve_images(
                client, settings, subject_config, scenes, orientation=args.orientation,
            )
            artifacts.save_json(job_id, "scenes.json", scenes)
        print("[image_gen] done")

        query = args.title or _title_from_script(script)

        # Free (no paid API call) — always run once we reach this point rather
        # than being individually resumable; there is nothing to load.
        timed_scenes = align_scenes(scenes, words, duration, args.language)
        print(f"[alignment] {len(timed_scenes)} scene(s) timed")

        # Screencast runs AFTER alignment: each scene's own step GIF is paced
        # to that scene's own just-computed duration.
        screencast_path = None
        if subject_config.media_source == "screencast":
            screencast_path = artifacts.path_for(job_id, "screencast.mp4")
            if should_run("screencast", args.from_step):
                gif_paths = [
                    artifacts.path_for(job_id, f"source_gifs/step-{i:03d}.gif")
                    for i in range(len(timed_scenes))
                ]
                await screencast_step.build_screencast(
                    settings, gif_paths, screencast_path, timed_scenes
                )
            elif not screencast_path.is_file():
                raise SystemExit(f"expected {screencast_path} to already exist")
            print(f"[screencast] {screencast_path}")

        # Reuses the real orchestrator's re-authoring logic (not duplicated
        # here) so a layout-gate failure during resume is handled exactly the
        # way a live job would handle it.
        pipeline = RealVideoPipeline(InMemoryJobRepository(), artifacts, client, settings)
        rounds = max(1, settings.outer_retry_limit)
        data_path = None
        for attempt in range(1, rounds + 1):
            last = attempt == rounds
            data = compose.build_data(
                query, job_id, subject_config, timed_scenes, duration, args.orientation, metadata,
                screencast_rel_path="assets/media/screencast.mp4",
            )
            compose.validate_data(data, load_scene_schema(subject_config))
            data_path = artifacts.save_json(job_id, "data.json", data)
            artifacts.save_json(job_id, "meta.json", compose.build_meta(data, query))

            issues = await layout_gate.run_layout_gate(settings, subject_config, data, data_path)
            if not issues:
                break
            if last:
                detail = "; ".join(
                    f'{i.get("code")} on scene "{i.get("sceneId")}" ({i.get("selector")})'
                    for i in issues[:5]
                )
                raise SystemExit(
                    f"{len(issues)} layout error(s) persisted after {rounds} round(s): {detail}"
                )
            print(f"[layout_gate] round {attempt}/{rounds}: {len(issues)} issue(s) — re-authoring")
            timed_scenes = await pipeline._reauthor_offending(
                job_id, subject_config, scenes_index, sentences_by_index, script,
                timed_scenes, issues, args.orientation, args.language,
            )
        print(f"[compose] data.json: {data_path}")

    out_path = artifacts.path_for(job_id, "video.mp4")
    if args.from_step != "thumbnail":
        await render.render_video(
            settings, subject_config, data_path, audio_path, out_path,
            screencast_file=screencast_path,
        )
        print(f"[render] video: {out_path}")
    elif not out_path.is_file():
        raise SystemExit(f"--from-step thumbnail requires an existing {out_path}")

    if settings.thumbnail_enabled and args.orientation == "horizontal":
        try:
            await thumbnail.generate_thumbnail(
                client, settings, subject_config, query, timed_scenes,
                artifacts.path_for(job_id, "thumbnail.jpg"), out_path.parent,
                orientation=args.orientation,
            )
            print("[thumbnail] done")
        except Exception as exc:  # noqa: BLE001 — best-effort, matches the orchestrator
            print(f"[thumbnail] best-effort failed: {exc}")

    print(f"\nDone. video.mp4 at {out_path}")


if __name__ == "__main__":
    asyncio.run(main())
