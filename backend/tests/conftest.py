from __future__ import annotations

import hashlib
import re
from collections.abc import Sequence
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.knowledge_base import DOCUMENTS, KnowledgeBase
from app.main import create_app
from app.tts import streaming_wav_header

DIM = 256
STOPWORDS = {
    "a",
    "an",
    "the",
    "is",
    "are",
    "what",
    "your",
    "you",
    "our",
    "we",
    "for",
    "to",
    "of",
    "and",
    "in",
    "on",
    "it",
    "do",
    "can",
    "how",
    "my",
    "with",
    "within",
}


def hashing_embedder(texts: Sequence[str]):
    """Deterministic bag-of-words embedder so tests never download a model."""
    for text in texts:
        vec = np.zeros(DIM, dtype=np.float32)
        for word in re.findall(r"[a-z0-9]+", text.lower()):
            if word in STOPWORDS:
                continue
            word = word.rstrip("s") or word  # crude stemming: "returns" ~ "return"
            vec[int(hashlib.md5(word.encode()).hexdigest(), 16) % DIM] += 1.0
        yield vec


class FakeTTS:
    sample_rate = 16000

    def __init__(self) -> None:
        self.spoken: list[str] = []

    def get_voice(self, language: str):
        return None

    async def synthesize_wav(self, text: str, language: str) -> bytes:
        self.spoken.append(text)
        return b"RIFF-fake-wav"

    async def stream(self, sentences, language, send_bytes) -> None:
        await send_bytes(streaming_wav_header(self.sample_rate))
        async for sentence in sentences:
            self.spoken.append(sentence)
            await send_bytes(b"\x00\x01" * 4)


@pytest.fixture
def kb() -> KnowledgeBase:
    return KnowledgeBase(DOCUMENTS, lambda: hashing_embedder)


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    frontend = tmp_path / "frontend"
    frontend.mkdir()
    (frontend / "index.html").write_text("<!doctype html><title>t</title>")
    return replace(
        Settings(),
        deepgram_api_key="dg-test",
        frontend_dir=frontend,
        voices_dir=tmp_path / "voices",
    )


@pytest.fixture
def fake_tts() -> FakeTTS:
    return FakeTTS()


@pytest.fixture
def client(settings: Settings, kb: KnowledgeBase, fake_tts: FakeTTS):
    app = create_app(settings, kb, fake_tts, warm_up=False)
    with TestClient(app) as test_client:
        yield test_client
