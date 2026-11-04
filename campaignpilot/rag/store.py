"""RAG over the marketing knowledge base, stored in Qdrant.

Pipeline: markdown files -> chunks (one per section) -> embeddings -> Qdrant.
Query: embed question -> cosine similarity search -> top-k chunks with sources.

QDRANT_URL modes:
  memory          in-process, nothing persisted (tests)
  local           on-disk folder (QDRANT_PATH), no server needed — single process only
  http://...:6333 Qdrant server (docker compose)
"""
from __future__ import annotations

import atexit
import hashlib
import logging
import re
import uuid
from functools import lru_cache
from pathlib import Path

from qdrant_client import QdrantClient
from qdrant_client.models import Distance, PointStruct, VectorParams

from campaignpilot.config import get_settings
from campaignpilot.llm import get_embedder

log = logging.getLogger(__name__)


@lru_cache
def get_qdrant() -> QdrantClient:
    s = get_settings()
    if s.qdrant_url == "memory":
        client = QdrantClient(":memory:")
    elif s.qdrant_url == "local":
        Path(s.qdrant_path).mkdir(parents=True, exist_ok=True)
        client = QdrantClient(path=s.qdrant_path)
    else:
        client = QdrantClient(url=s.qdrant_url)
    atexit.register(client.close)  # release the local folder lock cleanly
    return client


def collection_name() -> str:
    # separate collection per embedder: vectors from different models aren't comparable
    return f"{get_settings().qdrant_collection}_{get_embedder().name}"


def chunk_markdown(text: str, source: str, max_chars: int = 900) -> list[dict]:
    """Split on headings; long sections are split further on paragraph breaks."""
    chunks, title, heading, buf = [], source, source, []

    def flush() -> None:
        body = "\n".join(buf).strip()
        if not body:
            return
        parts, current = [], ""
        for para in re.split(r"\n\s*\n", body):
            if current and len(current) + len(para) > max_chars:
                parts.append(current)
                current = para
            else:
                current = f"{current}\n\n{para}" if current else para
        parts.append(current)
        for p in parts:
            chunks.append({"source": source, "heading": heading, "text": f"{title} > {heading}\n{p.strip()}"})

    for line in text.splitlines():
        if line.startswith("# "):
            title = line[2:].strip()
        elif line.startswith("## "):
            flush()
            buf = []
            heading = line[3:].strip()
        else:
            buf.append(line)
    flush()
    return chunks


def _point_id(chunk: dict) -> str:
    # deterministic id -> re-indexing the same content overwrites instead of duplicating
    digest = hashlib.sha256(f"{chunk['source']}|{chunk['heading']}|{chunk['text']}".encode()).hexdigest()
    return str(uuid.UUID(digest[:32]))


def index_knowledge_base(knowledge_dir: str) -> int:
    files = sorted(Path(knowledge_dir).glob("*.md"))
    chunks = [c for f in files for c in chunk_markdown(f.read_text(encoding="utf-8"), f.name)]
    if not chunks:
        log.warning("no knowledge files found in %s", knowledge_dir)
        return 0
    vectors = get_embedder().embed([c["text"] for c in chunks])
    client, name = get_qdrant(), collection_name()
    if client.collection_exists(name):
        client.delete_collection(name)  # full rebuild keeps removed docs out of results
    client.create_collection(name, vectors_config=VectorParams(size=len(vectors[0]), distance=Distance.COSINE))
    client.upsert(name, points=[
        PointStruct(id=_point_id(c), vector=v, payload=c) for c, v in zip(chunks, vectors, strict=True)
    ])
    log.info("indexed %d chunks from %d files into %s", len(chunks), len(files), name)
    return len(chunks)


def search(query: str, k: int = 4) -> list[dict]:
    client, name = get_qdrant(), collection_name()
    if not client.collection_exists(name):
        index_knowledge_base(get_settings().knowledge_dir)  # first run convenience
    vector = get_embedder().embed([query])[0]
    hits = client.query_points(name, query=vector, limit=k, with_payload=True).points
    return [
        {"source": h.payload["source"], "heading": h.payload["heading"], "text": h.payload["text"], "score": round(h.score, 3)}
        for h in hits
    ]
