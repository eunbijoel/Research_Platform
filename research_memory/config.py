from __future__ import annotations

import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _env(name: str, default: str) -> str:
    return os.environ.get(name, default).strip()


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


DATA_DIR = PROJECT_ROOT / _env("RM_DATA_DIR", "data")
RAW_DIR = DATA_DIR / "raw"
KB_DIR = DATA_DIR / "kb"
DB_PATH = KB_DIR / "memory.sqlite3"
INDEX_PATH = KB_DIR / "tfidf_index.pkl"  # lexical fallback
VECTOR_INDEX_PATH = KB_DIR / "vector_index.pkl"

OLLAMA_URL = _env("RM_OLLAMA_URL", "http://localhost:11434/api/generate")
OLLAMA_BASE_URL = _env(
    "RM_OLLAMA_BASE_URL",
    OLLAMA_URL.replace("/api/generate", "") if "/api/" in OLLAMA_URL else "http://localhost:11434",
)
MODEL_NAME = _env("RM_MODEL_NAME", "gemma4:e4b")
EMBED_MODEL = _env("RM_EMBED_MODEL", "nomic-embed-text")
LLM_TIMEOUT_SEC = int(_env("RM_LLM_TIMEOUT_SEC", "120"))
EMBED_TIMEOUT_SEC = int(_env("RM_EMBED_TIMEOUT_SEC", "120"))
EMBED_BATCH_SIZE = int(_env("RM_EMBED_BATCH_SIZE", "16"))
MOCK_LLM = _env_bool("RM_MOCK_LLM", False)

CHUNK_SIZE = 900
CHUNK_OVERLAP = 120
RETRIEVAL_TOP_K = 6


def ensure_data_dirs() -> None:
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    KB_DIR.mkdir(parents=True, exist_ok=True)
