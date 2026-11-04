"""GeminiClient retry / fallback logic, tested without network calls."""
import pytest

from campaignpilot.agents.schemas import CampaignReview
from campaignpilot.llm import GeminiClient


class Scripted(GeminiClient):
    def __init__(self, script: dict[str, list], **kw):
        super().__init__("main", "key", 0.4, fallbacks=["backup"], retries=2, sleep=self.record_sleep, **kw)
        self.script, self.calls, self.sleeps = script, [], []

    def record_sleep(self, s):
        self.sleeps.append(s)

    def _invoke(self, model, schema, system, user):
        self.calls.append(model)
        outcome = self.script[model].pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return schema.model_validate(outcome)


OK = {"score": 8, "issues": [], "suggestions": []}
BUSY = RuntimeError("503 UNAVAILABLE. This model is currently experiencing high demand.")


def test_retries_transient_errors_then_succeeds():
    c = Scripted({"main": [BUSY, BUSY, OK]})
    assert c.generate_json(CampaignReview, "s", "u").score == 8
    assert c.calls == ["main"] * 3 and len(c.sleeps) == 2
    assert c.sleeps[1] > c.sleeps[0] * 1.2  # exponential backoff


def test_falls_back_when_main_stays_busy():
    c = Scripted({"main": [BUSY, BUSY, BUSY], "backup": [OK]})
    assert c.generate_json(CampaignReview, "s", "u").score == 8
    assert c.calls == ["main", "main", "main", "backup"]


def test_retired_model_skips_straight_to_fallback():
    gone = RuntimeError("404 NOT_FOUND: This model is no longer available to new users.")
    c = Scripted({"main": [gone], "backup": [OK]})
    c.generate_json(CampaignReview, "s", "u")
    assert c.calls == ["main", "backup"] and c.sleeps == []


def test_bad_output_gets_one_more_try():
    c = Scripted({"main": [ValueError("model returned no structured output"), OK]})
    c.generate_json(CampaignReview, "s", "u")
    assert c.calls == ["main", "main"]


def test_clear_message_when_everything_is_busy():
    c = Scripted({"main": [BUSY] * 3, "backup": [BUSY] * 3})
    with pytest.raises(RuntimeError, match="high demand; wait a minute"):
        c.generate_json(CampaignReview, "s", "u")
