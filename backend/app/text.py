"""Pure text helpers: prompt construction, sentence chunking and stream parsing."""

from __future__ import annotations

import json
import re
from collections.abc import Iterator

SYSTEM_PROMPT = "You are a fast, helpful AI assistant."

# Sentence terminators, including the Devanagari danda used in Hindi.
_SENTENCE_END = re.compile(r"[.!?।\n]+\s*")


def build_prompt(user_query: str, retrieved_context: str, language: str) -> str:
    """Build the RAG prompt sent to the language model."""
    return (
        "You are a helpful customer service agent. Use the following context to answer "
        "the user's question.\n"
        f"Context:\n{retrieved_context}\n\n"
        f"User Question: {user_query}\n\n"
        "Answer concisely in one to three short sentences suitable for being spoken aloud. "
        "CRITICAL: You MUST respond in the language corresponding to this language code: "
        f"'{language}'."
    )


def mock_response(retrieved_context: str, language: str) -> str:
    """Offline fallback answer: echo the best knowledge-base match."""
    first_line = retrieved_context.split("\n", 1)[0].strip()
    if not first_line:
        first_line = "I'm sorry, I couldn't find an answer to that."
    if language == "hi":
        return "हमारे रिकॉर्ड के अनुसार: " + first_line
    return "Based on our records: " + first_line


class SentenceChunker:
    """Accumulates streamed tokens and emits complete sentences.

    Speaking whole sentences keeps text-to-speech natural while still letting audio
    start before the language model has finished generating.
    """

    def __init__(self) -> None:
        self._buffer = ""

    def feed(self, token: str) -> Iterator[str]:
        self._buffer += token
        while True:
            match = _SENTENCE_END.search(self._buffer)
            if not match:
                return
            sentence = self._buffer[: match.end()].strip()
            self._buffer = self._buffer[match.end() :]
            if sentence:
                yield sentence

    def flush(self) -> str | None:
        remainder = self._buffer.strip()
        self._buffer = ""
        return remainder or None


def parse_sse_delta(line: str) -> str | None:
    """Extract the content delta from one OpenAI chat-completions SSE line.

    Returns ``None`` for non-data lines, keep-alives, malformed JSON and the
    ``[DONE]`` sentinel.
    """
    if not line.startswith("data:"):
        return None
    payload = line[len("data:") :].strip()
    if not payload or payload == "[DONE]":
        return None
    try:
        chunk = json.loads(payload)
        choices = chunk.get("choices") or []
        if not choices:
            return None
        content = (choices[0].get("delta") or {}).get("content")
    except (json.JSONDecodeError, AttributeError):
        return None
    return content or None


def encode_header_text(text: str) -> str:
    """Hex-encode UTF-8 text so it can travel safely in an HTTP header."""
    return text.encode("utf-8").hex()


def decode_header_text(value: str) -> str:
    return bytes.fromhex(value).decode("utf-8")
