import unittest

from app.pipeline.steps.segment import (
    SceneIndex,
    assert_three_way_equality,
    build_scene_index_from_steps,
)


class BuildSceneIndexFromStepsTests(unittest.TestCase):
    """Deterministic Pass-1 for user-guide: one scene per step, no LLM call."""

    def test_scene_count_matches_step_count(self) -> None:
        narrations = [
            "Click the gear icon to open settings.",
            "Enter the teammate's email and click Send.",
            "Confirm the invitation in the dialog that appears.",
        ]

        sentences, scenes_index = build_scene_index_from_steps(narrations, "horizontal")

        self.assertEqual(len(scenes_index), 3)
        self.assertTrue(all(isinstance(s, SceneIndex) for s in scenes_index))
        self.assertEqual([s.scene_id for s in scenes_index], ["scene-1", "scene-2", "scene-3"])

    def test_three_way_equality_holds_against_reassembled_script(self) -> None:
        narrations = [
            "Click the gear icon to open settings.",
            "Enter the teammate's email and click Send.",
        ]
        sentences, scenes_index = build_scene_index_from_steps(narrations, "horizontal")
        script = "\n\n".join(narrations)

        # Must not raise.
        assert_three_way_equality(scenes_index, sentences, script)

    def test_each_scenes_captions_reconstruct_only_its_own_step(self) -> None:
        narrations = ["First step text here.", "Second step text here."]
        _, scenes_index = build_scene_index_from_steps(narrations, "horizontal")

        first_words = " ".join(scenes_index[0].captions).split()
        second_words = " ".join(scenes_index[1].captions).split()
        self.assertEqual(" ".join(first_words), "First step text here.")
        self.assertEqual(" ".join(second_words), "Second step text here.")
        # No leakage across the step boundary.
        self.assertNotIn("Second", first_words)
        self.assertNotIn("First", second_words)

    def test_sentence_numbering_is_contiguous_and_global(self) -> None:
        # A step with multiple sentences shouldn't reset numbering, and the
        # next step's numbering must continue from where it left off.
        narrations = [
            "First sentence. Second sentence.",
            "Third sentence.",
        ]
        sentences, scenes_index = build_scene_index_from_steps(narrations, "horizontal")

        self.assertEqual([s["i"] for s in sentences], [1, 2, 3])
        self.assertEqual(scenes_index[0].idx_sentences, [1, 2])
        self.assertEqual(scenes_index[1].idx_sentences, [3])

    def test_empty_steps_list_returns_empty(self) -> None:
        sentences, scenes_index = build_scene_index_from_steps([], "horizontal")
        self.assertEqual(sentences, [])
        self.assertEqual(scenes_index, [])

    def test_no_llm_call_needed_pure_function_signature(self) -> None:
        # Sanity check that this is a plain sync function (no client/settings
        # params) — the whole point is zero API cost for this subject's SEGMENT.
        import inspect

        assert not inspect.iscoroutinefunction(build_scene_index_from_steps)


if __name__ == "__main__":
    unittest.main()
