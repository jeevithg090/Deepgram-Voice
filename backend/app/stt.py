"""Speech-to-text with the Deepgram REST and streaming (WebSocket) APIs."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from collections.abc import Awaitable, Callable
from urllib.parse import urlencode

import httpx
import websockets
from websockets.exceptions import ConnectionClosed, InvalidHandshake

from .config import Settings

logger = logging.getLogger(__name__)

DEEPGRAM_REST_URL = "https://api.deepgram.com/v1/listen"
DEEPGRAM_WS_URL = "wss://api.deepgram.com/v1/listen"


class TranscriptionError(RuntimeError):
    """Raised when Deepgram cannot be reached or rejects the request."""


def build_listen_query(
    model: str,
    language: str,
    *,
    streaming: bool,
    encoding: str | None = None,
    sample_rate: int | None = None,
) -> str:
    """Build the Deepgram ``/v1/listen`` query string.

    Language auto-detection is only available for pre-recorded audio, so streaming
    requests fall back to English when ``language == "auto"``.
    """
    params: dict[str, str] = {"model": model, "smart_format": "true"}
    if language == "auto":
        if streaming:
            params["language"] = "en"
        else:
            params["detect_language"] = "true"
    else:
        params["language"] = language
    if streaming:
        if encoding:
            params["encoding"] = encoding
        if sample_rate:
            params["sample_rate"] = str(int(sample_rate))
    return urlencode(params)


def extract_transcript(payload: dict) -> str:
    """Pull the best transcript out of a Deepgram response (REST or streaming)."""
    try:
        if "results" in payload:
            alternatives = payload["results"]["channels"][0]["alternatives"]
        else:
            alternatives = payload["channel"]["alternatives"]
        return (alternatives[0].get("transcript") or "").strip()
    except (KeyError, IndexError, TypeError):
        return ""


def _require_key(settings: Settings) -> str:
    if not settings.deepgram_api_key:
        raise TranscriptionError("DEEPGRAM_API_KEY is not configured on the server")
    return settings.deepgram_api_key


async def transcribe_audio(
    audio: bytes,
    language: str,
    settings: Settings,
    content_type: str = "audio/webm",
    client: httpx.AsyncClient | None = None,
) -> str:
    """Transcribe a complete audio recording."""
    api_key = _require_key(settings)
    query = build_listen_query(settings.deepgram_model, language, streaming=False)
    owns_client = client is None
    client = client or httpx.AsyncClient(timeout=settings.http_timeout_seconds)
    try:
        response = await client.post(
            f"{DEEPGRAM_REST_URL}?{query}",
            headers={"Authorization": f"Token {api_key}", "Content-Type": content_type},
            content=audio,
        )
        if response.status_code >= 400:
            raise TranscriptionError(f"Deepgram returned HTTP {response.status_code}")
        return extract_transcript(response.json())
    except httpx.HTTPError as exc:
        raise TranscriptionError(f"Deepgram request failed: {exc}") from exc
    finally:
        if owns_client:
            await client.aclose()


async def stream_transcription(
    receive_audio: Callable[[], Awaitable[bytes | None]],
    language: str,
    settings: Settings,
    encoding: str | None = None,
    sample_rate: int | None = None,
) -> str:
    """Pipe audio chunks to Deepgram until ``receive_audio`` returns empty/None.

    Returns the concatenated final transcript.
    """
    api_key = _require_key(settings)
    query = build_listen_query(
        settings.deepgram_model,
        language,
        streaming=True,
        encoding=encoding,
        sample_rate=sample_rate,
    )
    finals: list[str] = []

    try:
        async with websockets.connect(
            f"{DEEPGRAM_WS_URL}?{query}",
            additional_headers={"Authorization": f"Token {api_key}"},
            open_timeout=settings.http_timeout_seconds,
        ) as dg_ws:

            async def sender() -> None:
                try:
                    while chunk := await receive_audio():
                        await dg_ws.send(chunk)
                finally:
                    # Ask Deepgram to flush remaining results and close the stream.
                    with contextlib.suppress(ConnectionClosed):
                        await dg_ws.send(json.dumps({"type": "CloseStream"}))

            async def receiver() -> None:
                with contextlib.suppress(ConnectionClosed):
                    async for message in dg_ws:
                        if isinstance(message, bytes):
                            continue
                        result = json.loads(message)
                        if result.get("type") == "Results" and result.get("is_final"):
                            text = extract_transcript(result)
                            if text:
                                finals.append(text)

            await asyncio.gather(sender(), receiver())
    except (OSError, InvalidHandshake, asyncio.TimeoutError) as exc:
        raise TranscriptionError(f"Could not connect to Deepgram: {exc}") from exc

    return " ".join(finals).strip()
