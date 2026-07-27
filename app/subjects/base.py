from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, get_args


SubjectName = Literal["lab-management", "tech", "user-guide"]
SUPPORTED_SUBJECTS = get_args(SubjectName)

Orientation = Literal["vertical", "horizontal"]


@dataclass(frozen=True)
class SubjectConfig:
    name: SubjectName
    display_name: str
    topic_label: str
    guard_description: str
    narration_style: str
    # Pass 1 (segment): group the script's numbered sentences into semantic
    # scenes. Pass 2 (scene_split_prompt): author each scene's typed data from
    # its own sentences.
    segment_prompt: str
    scene_split_prompt: str
    scene_examples: str
    scene_schema_path: Path
    renderer_template: str
    required_content_fields: dict[str, list[str]]
    # Frame types whose picture is produced by the IMAGE_GEN step (from each
    # scene's imagePrompt). Empty => this subject has no image frames and the
    # image step is a no-op for it.
    image_frame_types: frozenset[str] = frozenset()
    # Where this subject's visuals come from. "none" (the default) means the
    # frames draw everything themselves. "screencast" means a user-uploaded GIF
    # is transcoded to MP4 and played as a root-level <video> under every scene
    # (see pipeline/steps/screencast.py); the frames are transparent overlays
    # and the IMAGE_GEN step is a no-op because image_frame_types is empty.
    media_source: Literal["none", "screencast"] = "none"
    # Target (min, max) spoken seconds per orientation. Vertical is a single-
    # pass short; horizontal orientations listed in long_form_orientations use
    # the sectioned long-form flow instead (see app/pipeline/steps/sections.py).
    duration_targets: dict[str, tuple[int, int]] = field(
        default_factory=lambda: {"vertical": (45, 90), "horizontal": (300, 600)}
    )
    long_form_orientations: frozenset[str] = frozenset({"horizontal"})
    section_word_target: int = 200
    # Karaoke spoken-word highlight color (hex) — deliberately independent of
    # each frame's accent so captions read as one system across the video.
    cap_highlight: str = "#FFD24A"
