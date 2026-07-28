"""Extract narration steps from a .docx software user guide.

Each step is delimited by a Heading-styled paragraph in the document (e.g. Word's
"Heading 1") — that heading's own text is a document title, never spoken. Within a
step, exactly one Normal paragraph names the screen-recording GIF that illustrates
it (e.g. "Ảnh minh họa Bước 1 (buoc-1-truy-cap-module.gif)"); that reference is
also never spoken, only used to resolve which uploaded GIF belongs to this step.
Every other Normal paragraph in the step's span is appended to its narration.

Text-only extraction — no embedded-image handling. The GIFs are separate uploads,
matched to steps by the filename named in each caption (app/api/router.py), not
extracted from the document itself.
"""
import re
from dataclasses import dataclass
from io import BytesIO

from docx import Document

# Matches a bare filename ending in .gif, wherever it appears in a caption line
# (e.g. inside "(buoc-1-truy-cap-module.gif)"). Requires the match to start on a
# word character so it never grabs a leading "(" or space.
_GIF_REF_RE = re.compile(r"([\w][\w\-. ]*\.gif)", re.IGNORECASE)


class DocxExtractError(Exception):
    pass


@dataclass
class DocStep:
    narration: str
    gif_filename: str


def extract_steps(data: bytes) -> list[DocStep]:
    try:
        doc = Document(BytesIO(data))
    except Exception as exc:  # noqa: BLE001 — any parse failure is the same user-facing error
        raise DocxExtractError(
            f"could not read the uploaded file as a .docx: {exc}"
        ) from exc

    steps: list[dict] = []
    current: dict | None = None
    for para in doc.paragraphs:
        text = para.text.strip()
        if not text:
            continue
        style_name = (para.style.name if para.style is not None else "") or ""
        if style_name.startswith("Heading"):
            current = {"heading": text, "narration_parts": [], "gif_filenames": []}
            steps.append(current)
            continue
        if current is None:
            # Front matter before the first heading (document title/subtitle) —
            # belongs to no step.
            continue
        match = _GIF_REF_RE.search(text)
        if match:
            current["gif_filenames"].append(match.group(1).strip())
            continue
        current["narration_parts"].append(text)

    if not steps:
        raise DocxExtractError(
            "no step headings found in the document — use Word's Heading style "
            "for each step's title (e.g. Heading 1)."
        )

    result: list[DocStep] = []
    for i, step in enumerate(steps, start=1):
        heading = step["heading"]
        gifs: list[str] = step["gif_filenames"]
        if not gifs:
            raise DocxExtractError(
                f'step {i} ("{heading}") has no .gif reference — add a line '
                'naming its screen-recording file, e.g. "(my-step.gif)".'
            )
        distinct = {g.lower() for g in gifs}
        if len(distinct) > 1:
            raise DocxExtractError(
                f'step {i} ("{heading}") references multiple different GIF '
                f"files ({', '.join(gifs)}) — each step must name exactly one."
            )
        narration = " ".join(step["narration_parts"]).strip()
        if not narration:
            raise DocxExtractError(
                f'step {i} ("{heading}") has no narration text — nothing to '
                "speak for this step."
            )
        result.append(DocStep(narration=narration, gif_filename=gifs[0]))
    return result
