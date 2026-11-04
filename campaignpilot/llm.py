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
import re
import time
from functools import lru_cache
from typing import Protocol, TypeVar

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
class GeminiClient:
    name = "gemini"

    def __init__(self, model: str, api_key: str, temperature: float):
        from langchain_google_genai import ChatGoogleGenerativeAI

        if not api_key:
            raise RuntimeError("GOOGLE_API_KEY is not set. Add it to .env or use LLM_PROVIDER=fake.")
        self._llm = ChatGoogleGenerativeAI(
            model=model, google_api_key=api_key, temperature=temperature, max_retries=3, timeout=60
        )

    def generate_json(self, schema: type[T], system: str, user: str) -> T:
        structured = self._llm.with_structured_output(schema)
        started = time.perf_counter()
        last_err: Exception | None = None
        for attempt in range(2):  # one extra try if the model returns invalid JSON
            try:
                result = structured.invoke([("system", system), ("human", user)])
                if result is None:
                    raise ValueError("model returned no structured output")
                log.info("gemini %s ok in %.2fs", schema.__name__, time.perf_counter() - started)
                return result
            except Exception as e:  # noqa: BLE001 - surface after retry
                last_err = e
                log.warning("gemini %s attempt %d failed: %s", schema.__name__, attempt + 1, e)
        raise RuntimeError(f"LLM failed to produce {schema.__name__}: {last_err}")


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
        return GeminiClient(s.gemini_model, s.google_api_key, s.llm_temperature)
    raise ValueError(f"Unknown LLM_PROVIDER: {s.llm_provider}")


@lru_cache
def get_embedder() -> Embedder:
    s = get_settings()
    if s.llm_provider == "gemini" and s.google_api_key:
        return GeminiEmbedder(s.gemini_embedding_model, s.google_api_key)
    return HashEmbedder()
