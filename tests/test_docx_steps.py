import unittest
from io import BytesIO

from docx import Document

from app.documents.docx import DocxExtractError, extract_steps


def _build_docx(sections: list[dict]) -> bytes:
    """A minimal in-memory .docx: each section is
    {heading, narration_lines: [...], captions: [...]}, written as a
    Heading-1 paragraph followed by plain paragraphs — python-docx both
    writes and reads, so no fixture file is needed."""
    doc = Document()
    for section in sections:
        doc.add_paragraph(section["heading"], style="Heading 1")
        for line in section.get("narration_lines", []):
            doc.add_paragraph(line)
        for caption in section.get("captions", []):
            doc.add_paragraph(caption)
    buf = BytesIO()
    doc.save(buf)
    return buf.getvalue()


class ExtractStepsTests(unittest.TestCase):
    def test_two_steps_extracted_in_order(self) -> None:
        data = _build_docx([
            {
                "heading": "Step 1: Open settings",
                "narration_lines": ["Click the gear icon in the top right."],
                "captions": ["Illustration Step 1 (step-1-open-settings.gif)"],
            },
            {
                "heading": "Step 2: Invite a teammate",
                "narration_lines": ["Enter their email and click Send."],
                "captions": ["Illustration Step 2 (step-2-invite.gif)"],
            },
        ])

        steps = extract_steps(data)

        self.assertEqual(len(steps), 2)
        self.assertEqual(steps[0].gif_filename, "step-1-open-settings.gif")
        self.assertEqual(steps[0].narration, "Click the gear icon in the top right.")
        self.assertEqual(steps[1].gif_filename, "step-2-invite.gif")
        self.assertEqual(steps[1].narration, "Enter their email and click Send.")

    def test_heading_and_caption_text_never_appear_in_narration(self) -> None:
        data = _build_docx([{
            "heading": "Step 1: Open settings",
            "narration_lines": ["Click the gear icon."],
            "captions": ["Illustration Step 1 (open-settings.gif)"],
        }])

        narration = extract_steps(data)[0].narration
        self.assertNotIn("Step 1", narration)
        self.assertNotIn("Illustration", narration)
        self.assertNotIn(".gif", narration)

    def test_multiple_narration_paragraphs_are_joined(self) -> None:
        data = _build_docx([{
            "heading": "Step 1",
            "narration_lines": ["First sentence.", "Second sentence."],
            "captions": ["(step-1.gif)"],
        }])

        narration = extract_steps(data)[0].narration
        self.assertIn("First sentence.", narration)
        self.assertIn("Second sentence.", narration)

    def test_front_matter_before_first_heading_is_ignored(self) -> None:
        doc = Document()
        doc.add_paragraph("A Guide", style="Title")
        doc.add_paragraph("Some subtitle text.")
        doc.add_paragraph("Step 1", style="Heading 1")
        doc.add_paragraph("Narration.")
        doc.add_paragraph("(step-1.gif)")
        buf = BytesIO()
        doc.save(buf)

        steps = extract_steps(buf.getvalue())
        self.assertEqual(len(steps), 1)
        self.assertEqual(steps[0].narration, "Narration.")

    def test_no_heading_anywhere_raises(self) -> None:
        doc = Document()
        doc.add_paragraph("Just a plain paragraph, no heading style at all.")
        buf = BytesIO()
        doc.save(buf)

        with self.assertRaises(DocxExtractError):
            extract_steps(buf.getvalue())

    def test_step_with_no_gif_reference_raises(self) -> None:
        data = _build_docx([{
            "heading": "Step 1",
            "narration_lines": ["Click the button."],
            "captions": [],
        }])

        with self.assertRaises(DocxExtractError):
            extract_steps(data)

    def test_step_with_two_distinct_gif_references_raises(self) -> None:
        data = _build_docx([{
            "heading": "Step 1",
            "narration_lines": ["Click the button."],
            "captions": ["(step-1a.gif)", "(step-1b.gif)"],
        }])

        with self.assertRaises(DocxExtractError):
            extract_steps(data)

    def test_step_with_repeated_identical_gif_reference_is_fine(self) -> None:
        # Same filename mentioned twice (e.g. a before/after caption) is not
        # ambiguous — only genuinely DIFFERENT filenames are.
        data = _build_docx([{
            "heading": "Step 1",
            "narration_lines": ["Click the button."],
            "captions": ["(step-1.gif)", "See (step-1.gif) again"],
        }])

        steps = extract_steps(data)
        self.assertEqual(steps[0].gif_filename, "step-1.gif")

    def test_step_with_only_heading_and_caption_raises(self) -> None:
        data = _build_docx([{
            "heading": "Step 1",
            "narration_lines": [],
            "captions": ["(step-1.gif)"],
        }])

        with self.assertRaises(DocxExtractError):
            extract_steps(data)

    def test_not_a_docx_raises(self) -> None:
        with self.assertRaises(DocxExtractError):
            extract_steps(b"not a docx file at all")

    def test_five_step_shape_matching_real_sample(self) -> None:
        # Mirrors the actual structure of Huong-dan-su-dung-Co-cau-to-chuc.docx:
        # 5 Heading-1 sections, one narration paragraph and one gif caption each.
        sections = [
            {
                "heading": f"Bước {i}: Step title {i}",
                "narration_lines": [f"Narration text for step {i}."],
                "captions": [f"Ảnh minh họa Bước {i} (buoc-{i}-demo.gif)"],
            }
            for i in range(1, 6)
        ]
        data = _build_docx(sections)

        steps = extract_steps(data)

        self.assertEqual(len(steps), 5)
        for i, step in enumerate(steps, start=1):
            self.assertEqual(step.gif_filename, f"buoc-{i}-demo.gif")
            self.assertEqual(step.narration, f"Narration text for step {i}.")


if __name__ == "__main__":
    unittest.main()
