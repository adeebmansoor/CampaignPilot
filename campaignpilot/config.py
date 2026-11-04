"""Central configuration, loaded from environment variables / .env.

Every setting has a safe default so the project runs locally (SQLite +
in-memory Qdrant + fake LLM) without any external services.
"""
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ROOT_DIR / ".env", extra="ignore")

    # --- LLM -------------------------------------------------------------
    # "gemini" for real calls, "fake" for offline demos and tests.
    llm_provider: str = "gemini"
    google_api_key: str = ""
    gemini_model: str = "gemini-2.5-flash"
    gemini_embedding_model: str = "models/gemini-embedding-001"
    llm_temperature: float = 0.4

    # --- Storage ---------------------------------------------------------
    database_url: str = f"sqlite:///{ROOT_DIR / 'data' / 'campaignpilot.db'}"
    # "memory" -> in-process Qdrant, "local" -> on-disk folder, or a URL like
    # http://localhost:6333 for the Qdrant server started by docker compose.
    qdrant_url: str = "local"
    qdrant_path: str = str(ROOT_DIR / "data" / "qdrant")
    qdrant_collection: str = "marketing_knowledge"
    checkpoint_db: str = str(ROOT_DIR / "data" / "checkpoints.sqlite")

    # --- Agents ----------------------------------------------------------
    # "mcp" -> agents call tools through the MCP server (stdio),
    # "direct" -> agents call the same tool functions in-process.
    tools_mode: str = "mcp"
    max_revisions: int = 2
    review_pass_score: int = 7

    # --- Pipeline --------------------------------------------------------
    raw_data_dir: str = str(ROOT_DIR / "data" / "raw")
    knowledge_dir: str = str(ROOT_DIR / "data" / "knowledge")
    outbox_dir: str = str(ROOT_DIR / "data" / "outbox")


@lru_cache
def get_settings() -> Settings:
    return Settings()
