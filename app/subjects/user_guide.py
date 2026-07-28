"""Software user-guide videos built over an uploaded GIF screen recording.

Unlike the other subjects, nothing visual is invented here. The user supplies a
GIF of the software plus the narration script; the pipeline transcodes the GIF to
a seekable MP4 that plays under the WHOLE video, and every frame this subject
authors is a transparent overlay on top of it — a scrim, a step title, and the
karaoke caption line.

That is why image_frame_types is empty (the IMAGE_GEN step no-ops) and why the
authoring prompt below is mostly a list of things NOT to do: the model's only job
is to label the step that's currently playing.
"""
from pathlib import Path

from app.config import Settings
from app.subjects.base import SubjectConfig

RENDERER_TEMPLATE = "user-guide"

NARRATION_STYLE = """You are narrating a software user guide over a screen recording. Write in clear, direct second person — "click", "open", "select", "you'll see". One instruction at a time, in the order the user performs it. Plain spoken text only: no headings, no step numbers read aloud, no stage directions.

Tone: calm, practical, like a colleague walking someone through the software for the first time. Name what's on screen using the software's own words (button labels, menu names) so the words match what the viewer is looking at. Avoid reading out symbols, file paths, or URLs character by character — say them the way a person would.

Return ONLY the narration script text, nothing else."""

SEGMENT_PROMPT = """This is a software user-guide video: a screen recording of the software plays continuously underneath, and the narration walks the viewer through it. Group the sentences into scenes at STEP boundaries — cut a new scene wherever the narration moves to the next action the user performs (a new click, a new screen, a new setting). Sentences that describe one action, plus its immediate result, belong in the same scene. Aim for one scene per step; a short guide is roughly 5 to 9 scenes."""

SCENE_SPLIT_PROMPT = """You are authoring the typed overlay content for a batch of scenes in a JSON-driven video template called HyperFrames. This is a software user guide.

CRITICAL CONTEXT: a screen recording of the software is already playing behind every single scene, for the entire video. You are NOT choosing or describing imagery. You are writing the short text overlay that sits on top of the recording while that step is being narrated. Anything you write covers up the demo, so write little and make it count.

You will be given:
1. The FULL SCRIPT — context, so you understand each step's place in the walkthrough.
2. Per scene: its "id", its OWN SENTENCES (the words spoken during it), and its CAPTIONS (already finalized, given verbatim; you do not write or change them).
3. A JSON Schema describing one scene object, including a "typeUsage" guide.

Return ONLY a single JSON object, no markdown fences, shaped exactly like:
{"scenes": [ { "id": "<the given id>", "type": "...", "eyebrow": "...", "headline": "...", ... type-specific fields ... } ] }
Emit one object per scene, in the SAME order as given, each echoing its given "id". Do NOT include "captions" — supplied by the system unchanged. Do NOT include "start", "duration", "audio", "captionTiming", or "sfx" — computed from the real recording.

Rules:
- Use "screencast" for almost every scene. It is the default and it keeps the demo visible. Use "title-card" ONLY for the opening scene and "outro" ONLY for the closing scene.
- NEVER write an "image" or "imagePrompt" field. This subject has no generated imagery; the picture is the user's own screen recording.
- Do NOT describe what is on screen ("a settings page appears"). The viewer can see it. Name the ACTION instead — "Open Settings", "Add a teammate", "Save and publish".
- "stepTitle" on a screencast scene is a short action label, 2 to 5 words, imperative. Omit it entirely when the narration is a transition or an aside rather than a distinct step — a title band over every second of the video is noise.
- "headline" on title-card / outro is the only place longer text belongs, and even there keep it under about 8 words.
- Keep every field short. Long text over a screen recording is unreadable and hides the thing being demonstrated.
- Colors are hex strings; omit bg/fg/accent to accept the template's defaults.

The input includes GOLDEN EXAMPLES: one well-formed scene per frame type. Author every scene at that level of completeness.

Return ONLY the JSON object, no commentary."""

SCENE_EXAMPLES = """{
  "note": "GOLDEN EXAMPLES — one well-formed scene per frame type. bg/fg/accent are omitted to take the template's defaults. 'captions' here are ILLUSTRATIVE ONLY: real captions are supplied by the system and returned unchanged. start/duration/captionTiming are always omitted. Remember a screen recording is playing behind all of these.",
  "examples": [
    {
      "id": "example-title-card",
      "type": "title-card",
      "eyebrow": "GETTING STARTED",
      "headline": "Invite your **first teammate**",
      "captions": ["In this guide, you'll add", "a teammate to your workspace."]
    },
    {
      "id": "example-screencast",
      "type": "screencast",
      "stepTitle": "Open **Settings**",
      "captions": ["Click your avatar in the top right,", "then choose **Settings**."]
    },
    {
      "id": "example-screencast-no-title",
      "type": "screencast",
      "captions": ["It might take a moment to load."]
    },
    {
      "id": "example-outro",
      "type": "outro",
      "headline": "That's **all it takes**",
      "subheadline": "Your teammate gets an email invite right away.",
      "captions": ["And that's how you add a teammate.", "**You're all set.**"]
    }
  ]
}"""

REQUIRED_CONTENT_FIELDS: dict[str, list[str]] = {
    # stepTitle is deliberately NOT required — a title band over every scene
    # covers the demo, so the model is allowed to omit it on transitions.
    "screencast": [],
    "title-card": ["headline"],
    "outro": ["headline", "subheadline"],
}


def schema_path(settings: Settings) -> Path:
    return settings.hyperframes_dir / "templates" / RENDERER_TEMPLATE / "schema.json"


def get_config(settings: Settings) -> SubjectConfig:
    return SubjectConfig(
        name="user-guide",
        display_name="software user guide",
        topic_label="Software user guide",
        # Never consulted: this subject only accepts script mode via the
        # screencast upload route, and the guard runs only in topic mode.
        guard_description=(
            "A software user guide walks a viewer through using a piece of "
            "software, step by step, over a recording of that software."
        ),
        narration_style=NARRATION_STYLE,
        segment_prompt=SEGMENT_PROMPT,
        scene_split_prompt=SCENE_SPLIT_PROMPT,
        scene_examples=SCENE_EXAMPLES,
        scene_schema_path=schema_path(settings),
        renderer_template=RENDERER_TEMPLATE,
        required_content_fields=REQUIRED_CONTENT_FIELDS,
        # Empty => the IMAGE_GEN step is a no-op for this subject.
        image_frame_types=frozenset(),
        media_source="screencast",
        # Horizontal only — screen recordings are landscape.
        duration_targets={"horizontal": (30, 900)},
        # The sectioned long-form flow only applies to topic mode, which this
        # subject never uses; keeping it empty avoids implying otherwise.
        long_form_orientations=frozenset(),
    )
