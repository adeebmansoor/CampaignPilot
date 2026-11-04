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
                        errors.append(f"{model}: busy after {self._retries} retries")
                        break
                    if not bad_output_retried:  # malformed / missing JSON: ask once more
                        bad_output_retried = True
                        log.warning("model %s gave unusable output for %s: %s", model, schema.__name__, msg[:160])
                        continue
                    errors.append(f"{model}: {msg[:200]}")
                    break
        busy = all("busy" in e or "not available" in e for e in errors)
        hint = " Google's servers are under high demand; wait a minute and run it again." if busy else ""
        raise RuntimeError(f"Gemini could not write the {schema.__name__} ({'; '.join(errors)}).{hint}")


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


# -------------------------------------------------------------------- factory
@lru_cache
def get_llm() -> LLMClient:
    s = get_settings()
    if s.llm_provider == "fake":
        return FakeLLMClient()
    if s.llm_provider == "gemini":
        fallbacks = [m.strip() for m in s.gemini_fallback_models.split(",")]
        return GeminiClient(s.gemini_model, s.google_api_key, s.llm_temperature, fallbacks, s.llm_retries)
    raise ValueError(f"Unknown LLM_PROVIDER: {s.llm_provider}")


@lru_cache
def get_embedder() -> Embedder:
    s = get_settings()
    if s.llm_provider == "gemini" and s.google_api_key:
        return GeminiEmbedder(s.gemini_embedding_model, s.google_api_key)
    return HashEmbedder()
