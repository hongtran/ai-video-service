"""Reasoning-family models and the 4o family accept mutually exclusive tuning
parameters: temperature is a 400 on the former, reasoning_effort is a 400 on the
latter. model_tuning_kwargs is the single place that decides, so it carries the
whole risk of the pipeline talking to either family."""
import json
import unittest
from types import SimpleNamespace

from app.config import Settings
from app.llm.client import model_tuning_kwargs
from app.pipeline.steps import segment
from app.subjects import get_subject_config


class FakeClient:
    """Replays queued JSON responses and records the kwargs each call received."""

    def __init__(self, responses: list[str]) -> None:
        self._responses = list(responses)
        self.kwargs: list[dict] = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    async def _create(self, *, model, messages, response_format=None, **kwargs):
        self.kwargs.append({"model": model, **kwargs})
        body = self._responses.pop(0)
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=body))]
        )


class ModelTuningKwargsTests(unittest.TestCase):
    def test_reasoning_model_gets_effort_and_no_temperature(self) -> None:
        kwargs = model_tuning_kwargs(
            "gpt-5.6-luna", temperature=0.5, reasoning_effort="high"
        )
        self.assertEqual(kwargs, {"reasoning_effort": "high"})

    def test_non_reasoning_model_gets_temperature_and_no_effort(self) -> None:
        # The important direction: gpt-4o starts with "gpt-4", not "o", so the
        # o-series prefixes must not catch it — and it must not be sent an
        # effort, which it would reject.
        kwargs = model_tuning_kwargs(
            "gpt-4o", temperature=0.5, reasoning_effort="high"
        )
        self.assertEqual(kwargs, {"temperature": 0.5})

    def test_o_series_is_treated_as_reasoning(self) -> None:
        for model in ("o1", "o1-mini", "o3-mini", "o4-mini"):
            with self.subTest(model=model):
                self.assertEqual(
                    model_tuning_kwargs(model, temperature=0.5, reasoning_effort="low"),
                    {"reasoning_effort": "low"},
                )

    def test_reasoning_model_without_effort_sends_nothing(self) -> None:
        self.assertEqual(model_tuning_kwargs("gpt-5.4-mini", temperature=0.5), {})

    def test_omitted_temperature_is_not_sent(self) -> None:
        # The subject guard relies on this: it passed no temperature before and
        # must keep passing none on a non-reasoning model.
        self.assertEqual(model_tuning_kwargs("gpt-4o", reasoning_effort="minimal"), {})

    def test_seed_rides_along_with_temperature_only(self) -> None:
        self.assertEqual(
            model_tuning_kwargs("gpt-4o", temperature=0.0, seed=7),
            {"temperature": 0.0, "seed": 7},
        )
        # Never sent to a reasoning model, which does not accept it.
        self.assertEqual(
            model_tuning_kwargs("gpt-5.6-luna", temperature=0.0, seed=7), {}
        )


class SegmentCallWiringTests(unittest.IsolatedAsyncioTestCase):
    """The regression that broke the pipeline: segment sent temperature=0.5 to a
    gpt-5 model and got a 400 that with_retries does not retry."""

    async def _segment_kwargs(self, model: str) -> dict:
        settings = Settings(scenes_llm_model=model, scenes_llm_reasoning_effort="medium")
        config = get_subject_config("lab-management", settings)
        sentences = segment.build_sentence_index("Hello there. Goodbye now.")
        client = FakeClient([
            json.dumps({"scenes": [{"idx_sentences": [1, 2]}]})
        ])
        await segment.segment_script(client, settings, config, sentences)
        return client.kwargs[0]

    async def test_gpt5_call_carries_effort_and_no_temperature(self) -> None:
        kwargs = await self._segment_kwargs("gpt-5.6-luna")
        self.assertEqual(kwargs["reasoning_effort"], "medium")
        self.assertNotIn("temperature", kwargs)

    async def test_gpt4o_call_carries_temperature_and_no_effort(self) -> None:
        kwargs = await self._segment_kwargs("gpt-4o")
        self.assertEqual(kwargs["temperature"], Settings().llm_temperature)
        self.assertNotIn("reasoning_effort", kwargs)


if __name__ == "__main__":
    unittest.main()
