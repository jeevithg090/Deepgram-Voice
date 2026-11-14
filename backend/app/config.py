"""Application settings, loaded from environment variables (and an optional `.env` file)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv

BACKEND_DIR = Path(__file__).resolve().parent.parent
PROJECT_ROOT = BACKEND_DIR.parent

# Load `.env` from the project root or the backend directory, without overriding
# variables that are already set in the real environment.
load_dotenv(PROJECT_ROOT / ".env")
load_dotenv(BACKEND_DIR / ".env")


def _env_bool(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _env_list(name: str, default: list[str]) -> list[str]:
    value = os.environ.get(name)
    if not value:
        return default
    return [item.strip() for item in value.split(",") if item.strip()]


def _env_str(name: str) -> str | None:
    value = os.environ.get(name, "").strip()
    return value or None


@dataclass(frozen=True)
class Settings:
    deepgram_api_key: str | None = None
    openai_api_key: str | None = None
    gemini_api_key: str | None = None

    deepgram_model: str = "nova-2"
    openai_model: str = "gpt-4o-mini"
    gemini_model: str = "gemini-2.5-flash"
    embedding_model: str = "BAAI/bge-small-en-v1.5"

    cors_origins: list[str] = field(default_factory=list)
    frontend_dir: Path = PROJECT_ROOT / "frontend"
    voices_dir: Path = BACKEND_DIR / "voices"

    enable_benchmark: bool = True
    benchmark_allow_server_key: bool = False
    max_upload_bytes: int = 10 * 1024 * 1024
    http_timeout_seconds: float = 30.0


@lru_cache
def get_settings() -> Settings:
    return Settings(
        deepgram_api_key=_env_str("DEEPGRAM_API_KEY"),
        openai_api_key=_env_str("OPENAI_API_KEY"),
        gemini_api_key=_env_str("GEMINI_API_KEY"),
        deepgram_model=os.environ.get("DEEPGRAM_MODEL", "nova-2"),
        openai_model=os.environ.get("OPENAI_MODEL", "gpt-4o-mini"),
        gemini_model=os.environ.get("GEMINI_MODEL", "gemini-2.5-flash"),
        embedding_model=os.environ.get("EMBEDDING_MODEL", "BAAI/bge-small-en-v1.5"),
        cors_origins=_env_list("CORS_ORIGINS", []),
        frontend_dir=Path(os.environ.get("FRONTEND_DIR", PROJECT_ROOT / "frontend")),
        voices_dir=Path(os.environ.get("VOICES_DIR", BACKEND_DIR / "voices")),
        enable_benchmark=_env_bool("ENABLE_BENCHMARK", True),
        benchmark_allow_server_key=_env_bool("BENCHMARK_ALLOW_SERVER_KEY", False),
        max_upload_bytes=int(os.environ.get("MAX_UPLOAD_BYTES", 10 * 1024 * 1024)),
        http_timeout_seconds=float(os.environ.get("HTTP_TIMEOUT_SECONDS", 30.0)),
    )
