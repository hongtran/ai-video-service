"""Registry-consistency guards for the tech template's frame types.

These catch the class of bug this repo's own tooling can't: a frame type
registered in one place (schema.json's enum, frame-defaults.mjs, or a
frames/*.html file) but not the others. None of these are exercised by the
existing test suite — see render_kit/templates/tech/README.md's authoring
contract and the .mjs file's own module docstring for the invariants each
test enforces.

Scoped to `tech` only: `lab-management`'s schema/.mjs enum already has 17
types while app/subjects/lab_management.py's REQUIRED_CONTENT_FIELDS has only
14 (reagent-prep, statistics, uncertainty are missing) — a known, separate
drift this file deliberately does not widen its scope to fix.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import unittest
from pathlib import Path

from app.config import Settings

TECH_DIR = Settings().hyperframes_dir / "templates" / "tech"

# Tokens every frame substitutes that aren't part of a scene's own content
# fields — universal plumbing injected by populate.js for every type.
SHARED_TOKENS = {
    "id", "width", "height", "duration", "orientation",
    "bg", "fg", "accent", "eyebrow", "headline",
    "capHighlight", "captionTiming", "captions",
}

# {{token}} substitutions a frame legitimately doesn't read even though the
# field is declared for it in TYPE_CONTENT_FIELDS (documentation-only field,
# or a field consumed by a different pipeline step, not the frame itself).
NON_RENDERED_TOKENS = {
    "code-snippet": {"language"},
    "photo": {"imagePrompt"},
    "photo-split": {"imagePrompt"},
}


def _load_schema() -> dict:
    return json.loads((TECH_DIR / "schema.json").read_text())


def _schema_type_enum(schema: dict) -> list[str]:
    return schema["properties"]["scenes"]["items"]["properties"]["type"]["enum"]


def _schema_type_usage(schema: dict) -> dict:
    return schema["properties"]["scenes"]["items"]["properties"]["type"]["typeUsage"]


def _load_mjs_exports() -> dict:
    """Read FRAME_DEFAULTS/TYPE_CONTENT_FIELDS/REQUIRED_CONTENT_FIELDS out of
    the live .mjs file by actually importing it in node, rather than
    regexing an ES module — comments and trailing commas make a regex parse
    brittle, and this is the same module populate.js itself imports."""
    mjs_path = (TECH_DIR / "frame-defaults.mjs").resolve()
    script = (
        "import { FRAME_DEFAULTS as F, TYPE_CONTENT_FIELDS as T, "
        "REQUIRED_CONTENT_FIELDS as R, VALID_TYPES as V } from "
        f"'{mjs_path.as_uri()}';"
        " process.stdout.write(JSON.stringify({F: Object.keys(F), T, R, V}));"
    )
    result = subprocess.run(
        ["node", "--input-type=module", "-e", script],
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(result.stdout)


class TechFrameEnumParityTests(unittest.TestCase):
    """(a) schema.json's type enum <-> frames/*.html <-> typeUsage — pure
    Python, no external process, always runs."""

    def test_every_enum_type_has_a_frame_file(self) -> None:
        schema = _load_schema()
        enum = _schema_type_enum(schema)
        missing = [t for t in enum if not (TECH_DIR / "frames" / f"{t}.html").is_file()]
        self.assertEqual(missing, [], f"enum types with no frames/<type>.html: {missing}")

    def test_no_orphan_frame_files(self) -> None:
        schema = _load_schema()
        enum = set(_schema_type_enum(schema))
        orphans = [
            p.stem
            for p in (TECH_DIR / "frames").glob("*.html")
            if p.stem not in enum
        ]
        self.assertEqual(orphans, [], f"frames/*.html with no matching enum entry: {orphans}")

    def test_every_enum_type_has_type_usage_guidance(self) -> None:
        schema = _load_schema()
        enum = set(_schema_type_enum(schema))
        usage_keys = set(_schema_type_usage(schema)) - {"FAMILIES"}
        self.assertEqual(
            enum, usage_keys,
            "typeUsage is what the scene-authoring LLM sees to pick a type — "
            "an enum entry missing from it is invisible to the model.",
        )


class TechFrameTokenParityTests(unittest.TestCase):
    """(b) TYPE_CONTENT_FIELDS <-> the {{token}}s each frames/*.html actually
    substitutes — pure Python, no external process. Per the .mjs file's own
    docstring this list is supposed to be exhaustive; this is the highest-
    value check here because a mismatch is a silent bug: an unregistered
    token renders as an empty string (populate.js's substituteTokens returns
    '' for any key missing from the data object), and a declared-but-unread
    field just never shows up on screen — neither crashes."""

    def test_declared_fields_match_frame_tokens(self) -> None:
        mjs = _load_mjs_exports()
        type_content_fields = mjs["T"]
        mismatches = []
        for frame_type, fields in type_content_fields.items():
            html = (TECH_DIR / "frames" / f"{frame_type}.html").read_text()
            tokens = set(re.findall(r"\{\{(\w+)\}\}", html)) - SHARED_TOKENS
            declared = set(fields)
            unregistered = tokens - declared
            never_read = declared - tokens - NON_RENDERED_TOKENS.get(frame_type, set())
            if unregistered or never_read:
                mismatches.append(
                    f"{frame_type}: unregistered tokens {sorted(unregistered)}, "
                    f"declared-but-unread fields {sorted(never_read)}"
                )
        self.assertEqual(mismatches, [], "\n" + "\n".join(mismatches))


@unittest.skipUnless(shutil.which("node"), "requires node")
class TechRequiredFieldsParityTests(unittest.TestCase):
    """(c) Python's REQUIRED_CONTENT_FIELDS (app/subjects/tech.py) <-> the
    .mjs mirror — tech-scoped only, see module docstring. Gated on node
    since reading the live .mjs export requires it; the same drift class is
    also caught by test_subject_support.py's schema-enum-vs-Python-dict
    assertion (pure Python, always runs) and by the enum/frame-file parity
    tests above, so skipping here when node is unavailable doesn't leave the
    invariant completely unchecked."""

    def test_python_mirror_matches_mjs_source_of_truth(self) -> None:
        from app.subjects.tech import REQUIRED_CONTENT_FIELDS as PY_REQUIRED

        mjs = _load_mjs_exports()
        js_required = mjs["R"]
        self.assertEqual(
            {k: sorted(v) for k, v in PY_REQUIRED.items()},
            {k: sorted(v) for k, v in js_required.items()},
            "app/subjects/tech.py's REQUIRED_CONTENT_FIELDS must byte-for-byte "
            "mirror frame-defaults.mjs's — scene_split.content_field_errors "
            "enforces the Python copy, so drift here means a scene missing a "
            "field the JS validator would have caught renders as a broken frame.",
        )


if __name__ == "__main__":
    unittest.main()
