"""LLM and embedding clients.

The rest of the code only talks to the small `LLMClient` interface, so the
provider is swappable. Two implementations:

* GeminiClient — real calls through LangChain's Gemini integration.
* FakeLLMClient — deterministic, offline responses for tests, CI and demos
  without an API key (LLM_PROVIDER=fake).
"""
from __future__ import annotations

import hashlib
import logging
import math
import random
import re
import time
from functools import lru_cache
from typing import Any, Protocol, TypeVar

from pydantic import BaseModel

from campaignpilot.config import get_settings

log = logging.getLogger(__name__)
T = TypeVar("T", bound=BaseModel)


class LLMClient(Protocol):
    name: str

    def generate_json(self, schema: type[T], system: str, user: str) -> T: ...


class Embedder(Protocol):
    name: str

    def embed(self, texts: list[str]) -> list[list[float]]: ...


# --------------------------------------------------------------------- Gemini
TRANSIENT_MARKERS = ("503", "UNAVAILABLE", "429", "RESOURCE_EXHAUSTED", "500", "INTERNAL",
                     "DEADLINE_EXCEEDED", "timed out", "timeout", "overloaded", "high demand")
UNAVAILABLE_MARKERS = ("404", "NOT_FOUND", "no longer available")
# Quota that won't reset within a run: daily limits, or a model with no free-tier access ("limit: 0").
HARD_QUOTA_MARKERS = ("limit: 0", "perday", "per_day", "per day", "free_tier", "billing")


def is_transient(err: Exception) -> bool:
    text = f"{type(err).__name__} {err}"
    return any(m.lower() in text.lower() for m in TRANSIENT_MARKERS)


class GeminiClient:
    """Gemini with resilience:
    * temporary errors (503 high demand, 429, timeouts) are retried with
      exponential backoff and jitter;
    * if a model stays unavailable, or has been retired (404), the next model
      in GEMINI_FALLBACK_MODELS is tried;
    * invalid or empty structured output gets one more attempt.
    """

    name = "gemini"

    def __init__(self, model: str, api_key: str, temperature: float,
                 fallbacks: list[str] | None = None, retries: int = 3, sleep=time.sleep):
        if not api_key:
            raise RuntimeError("GOOGLE_API_KEY is not set. Add it to .env or use LLM_PROVIDER=fake.")
        self._api_key, self._temperature = api_key, temperature
        self.models = [model] + [m for m in (fallbacks or []) if m and m != model]
        self._retries = max(0, retries)
        self._sleep = sleep
        self._clients: dict[str, Any] = {}

    def _client(self, model: str):
        if model not in self._clients:
            from langchain_google_genai import ChatGoogleGenerativeAI

            # retries are handled here, so the SDK's own retry loop is kept short
            self._clients[model] = ChatGoogleGenerativeAI(
                model=model, google_api_key=self._api_key, temperature=self._temperature, max_retries=1, timeout=90
            )
        return self._clients[model]

    def _invoke(self, model: str, schema: type[T], system: str, user: str) -> T:
        result = self._client(model).with_structured_output(schema).invoke([("system", system), ("human", user)])
        if result is None:
            raise ValueError("model returned no structured output")
        return result

    def generate_json(self, schema: type[T], system: str, user: str) -> T:
        started = time.perf_counter()
        errors: list[str] = []
        last_raw = ""
        for model in self.models:
            bad_output_retried = False
            attempt = 0
            while True:
                try:
                    result = self._invoke(model, schema, system, user)
                    log.info("gemini %s via %s ok in %.2fs", schema.__name__, model, time.perf_counter() - started)
                    return result
                except Exception as e:  # noqa: BLE001 - classified below
                    msg = str(e)
                    low = msg.lower().replace(" ", "")
                    if ("429" in msg or "resource_exhausted" in low or "quota" in low) and any(
                            m.replace(" ", "") in low for m in HARD_QUOTA_MARKERS):
                        log.warning("model %s: quota exhausted or not on your plan: %s", model, msg[:300])
                        errors.append(f"{model}: quota not available for this API key")
                        break
                    if any(m.lower() in msg.lower() for m in UNAVAILABLE_MARKERS):
                        log.warning("model %s is not available (%s); trying the next model", model, msg[:120])
                        errors.append(f"{model}: not available")
                        break
                    if is_transient(e) and attempt < self._retries:
                        wait = min(30.0, 2.0 * (2 ** attempt)) * random.uniform(0.75, 1.25)
                        attempt += 1
                        log.warning("model %s busy (%s); retry %d/%d in %.1fs",
                                    model, msg[:80], attempt, self._retries, wait)
                        self._sleep(wait)
                        continue
                    if is_transient(e):
                        kind = "rate-limited" if ("429" in msg or "quota" in low) else "busy"
                        errors.append(f"{model}: {kind} after {self._retries} retries")
                        last_raw = msg
                        break
                    if not bad_output_retried:  # malformed / missing JSON: ask once more
                        bad_output_retried = True
                        log.warning("model %s gave unusable output for %s: %s", model, schema.__name__, msg[:160])
                        continue
                    errors.append(f"{model}: {msg[:200]}")
                    break
        if any("quota" in e or "rate-limited" in e for e in errors):
            hint = (" Your API key has hit its usage limit or can't use these models on the free tier."
                    " Check https://aistudio.google.com/usage, or set GEMINI_MODEL to a model your key can use.")
        elif all("busy" in e or "not available" in e for e in errors):
            hint = " Google's servers are under high demand; wait a minute and run it again."
        else:
            hint = ""
        detail = f" Last error from Google: {last_raw[:240]}" if last_raw else ""
        raise RuntimeError(f"Gemini could not write the {schema.__name__} ({'; '.join(errors)}).{hint}{detail}")


class GeminiEmbedder:
    def __init__(self, model: str, api_key: str):
        from langchain_google_genai import GoogleGenerativeAIEmbeddings

        self.name = "gemini"
        self._emb = GoogleGenerativeAIEmbeddings(model=model, google_api_key=api_key)

    def embed(self, texts: list[str]) -> list[list[float]]:
        return self._emb.embed_documents(texts)


# ----------------------------------------------------------------------- Fake
class HashEmbedder:
    """Bag-of-words hashing embedder. Not semantic, but deterministic and good
    enough for keyword-style retrieval in tests and offline demos."""

    name = "hash"
    dim = 384

    def embed(self, texts: list[str]) -> list[list[float]]:
        out = []
        for text in texts:
            vec = [0.0] * self.dim
            for tok in re.findall(r"[a-z0-9]+", text.lower()):
                if len(tok) < 3:
                    continue
                h = int(hashlib.md5(tok.encode()).hexdigest(), 16)
                vec[h % self.dim] += 1.0
            norm = math.sqrt(sum(v * v for v in vec)) or 1.0
            out.append([v / norm for v in vec])
        return out


class FakeLLMClient:
    name = "fake"

    def generate_json(self, schema: type[T], system: str, user: str) -> T:
        from campaignpilot.fake_responses import respond

        delay = get_settings().fake_llm_delay
        if delay:
            time.sleep(delay)

        return schema.model_validate(respond(schema.__name__, system, user))


# -------------------------------------------------------------------- Gateway
class GatewayClient:
    """Sends every LLM call through the LLM Gateway project
    (https://github.com/adeebmansoor/llm-gateway), which handles retries,
    provider fallback (Gemini -> Groq), caching, rate limits and budgets.

    The Pydantic schema is sent as an OpenAI-style json_schema response
    format; the gateway validates the JSON, and this client validates it
    again with Pydantic (one retry if that still fails).
    """

    name = "gateway"

    def __init__(self, base_url: str, api_key: str, route: str, temperature: float, timeout: float = 180.0,
                 transport=None):
        import httpx

        if not base_url or not api_key:
            raise RuntimeError("Set GATEWAY_URL and GATEWAY_API_KEY to use LLM_PROVIDER=gateway.")
        self.route, self.temperature = route, temperature
        self._http = httpx.Client(base_url=base_url.rstrip("/"), timeout=timeout, transport=transport,
                                  headers={"Authorization": f"Bearer {api_key}"})

    def _post(self, path: str, body: dict) -> dict:
        import httpx

        for attempt in range(2):
            try:
                r = self._http.post(path, json=body)
            except httpx.TransportError as e:
                if attempt == 0:
                    time.sleep(2)
                    continue
                raise RuntimeError(f"Can't reach the LLM Gateway at {self._http.base_url}: {e}") from e
            if r.status_code >= 400:
                try:
                    err = r.json().get("error", {})
                except ValueError:
                    err = {"message": r.text[:300]}
                tried = "; ".join(f"{a.get('target')}: {a.get('outcome')}" for a in err.get("attempts", [])[-4:])
                raise RuntimeError(f"LLM Gateway returned {r.status_code}: {err.get('message', 'error')}"
                                   + (f" (tried {tried})" if tried else ""))
            return r.json()
        raise RuntimeError("unreachable")

    def generate_json(self, schema: type[T], system: str, user: str) -> T:
        body = {
            "model": self.route,
            "temperature": self.temperature,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "response_format": {"type": "json_schema",
                                "json_schema": {"name": schema.__name__, "schema": schema.model_json_schema()}},
        }
        last: Exception | None = None
        for _ in range(2):
            data = self._post("/v1/chat/completions", body)
            g = data.get("gateway", {})
            log.info("gateway %s via %s (cache %s, fallback %s, %sms)", schema.__name__, g.get("target"),
                     g.get("cache"), g.get("fallback"), g.get("latency_ms"))
            try:
                return schema.model_validate_json(data["choices"][0]["message"]["content"])
            except Exception as e:  # noqa: BLE001 - pydantic constraints the JSON schema can't express
                last = e
                body = {**body, "temperature": min(1.0, self.temperature + 0.2)}  # different sample, and not cached
        raise RuntimeError(f"The gateway's answer didn't fit {schema.__name__}: {last}")


class GatewayEmbedder:
    name = "gateway"

    def __init__(self, client: GatewayClient, route: str = "default"):
        self._client, self._route = client, route

    def embed(self, texts: list[str]) -> list[list[float]]:
        out: list[list[float]] = []
        for i in range(0, len(texts), 64):
            data = self._client._post("/v1/embeddings", {"model": self._route, "input": texts[i:i + 64]})
            out.extend(d["embedding"] for d in sorted(data["data"], key=lambda d: d["index"]))
        return out


# -------------------------------------------------------------------- factory
@lru_cache
def get_llm() -> LLMClient:
    s = get_settings()
    if s.llm_provider == "fake":
        return FakeLLMClient()
    if s.llm_provider == "gemini":
        fallbacks = [m.strip() for m in s.gemini_fallback_models.split(",")]
        return GeminiClient(s.gemini_model, s.google_api_key, s.llm_temperature, fallbacks, s.llm_retries)
    if s.llm_provider == "gateway":
        return GatewayClient(s.gateway_url, s.gateway_api_key, s.gateway_route, s.llm_temperature)
    raise ValueError(f"Unknown LLM_PROVIDER: {s.llm_provider}")


@lru_cache
def get_embedder() -> Embedder:
    s = get_settings()
    if s.llm_provider == "gateway":
        return GatewayEmbedder(get_llm(), s.gateway_embedding_route)
    if s.llm_provider == "gemini" and s.google_api_key:
        return GeminiEmbedder(s.gemini_embedding_model, s.google_api_key)
    return HashEmbedder()
