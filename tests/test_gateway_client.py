"""CampaignPilot's LLM Gateway client, against a mocked gateway (no network)."""
import json

import httpx
import pytest

from campaignpilot.agents.schemas import CampaignReview
from campaignpilot.llm import GatewayClient, GatewayEmbedder


def completion(content: str, target="groq/llama-3.3-70b-versatile"):
    return {"choices": [{"message": {"role": "assistant", "content": content}}],
            "gateway": {"target": target, "cache": "miss", "fallback": True, "latency_ms": 40}}


def client(handler):
    return GatewayClient("http://gw:8100", "gw-test-key", "default", 0.4, transport=httpx.MockTransport(handler))


def test_sends_openai_style_json_schema_request():
    seen = []

    def handler(req: httpx.Request):
        seen.append(req)
        return httpx.Response(200, json=completion(json.dumps({"score": 8, "issues": [], "suggestions": ["x"]})))

    review = client(handler).generate_json(CampaignReview, "be strict", "draft here")
    assert review.score == 8
    body = json.loads(seen[0].content)
    assert seen[0].url.path == "/v1/chat/completions" and seen[0].headers["authorization"] == "Bearer gw-test-key"
    assert body["model"] == "default" and body["messages"][0] == {"role": "system", "content": "be strict"}
    assert body["response_format"]["json_schema"]["schema"]["properties"]["score"]["maximum"] == 10


def test_retries_once_when_answer_breaks_pydantic_rules():
    answers = iter([json.dumps({"score": 99}), json.dumps({"score": 7})])
    temps = []

    def handler(req):
        temps.append(json.loads(req.content)["temperature"])
        return httpx.Response(200, json=completion(next(answers)))

    assert client(handler).generate_json(CampaignReview, "s", "u").score == 7
    assert temps[1] > temps[0]  # second try is a fresh sample, not a cache hit


def test_gateway_errors_are_explained():
    def handler(req):
        return httpx.Response(503, json={"error": {"message": "Every target in route 'default' failed.",
                                                   "attempts": [{"target": "gemini/gemini-3.8-flash", "outcome": "quota"},
                                                                {"target": "groq/llama-3.3-70b-versatile", "outcome": "auth"}]}})

    with pytest.raises(RuntimeError, match="503.*groq/llama-3.3-70b-versatile: auth"):
        client(handler).generate_json(CampaignReview, "s", "u")


def test_embeddings_are_batched_and_ordered():
    def handler(req):
        texts = json.loads(req.content)["input"]
        data = [{"index": i, "embedding": [float(len(t))]} for i, t in enumerate(texts)]
        return httpx.Response(200, json={"data": list(reversed(data))})

    vectors = GatewayEmbedder(client(handler)).embed(["a", "bb", "ccc"])
    assert vectors == [[1.0], [2.0], [3.0]]


def test_requires_url_and_key():
    with pytest.raises(RuntimeError, match="GATEWAY_URL"):
        GatewayClient("", "", "default", 0.4)
