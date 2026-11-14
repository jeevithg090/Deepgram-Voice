"""Language-model integrations (OpenAI streaming / non-streaming and Google Gemini).

Every function degrades gracefully: if no API key is configured or the provider
fails, a deterministic answer built from the retrieved context is returned so the
voice pipeline keeps working offline.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator

import httpx

from .config import Settings
from .text import SYSTEM_PROMPT, build_prompt, mock_response, parse_sse_delta

logger = logging.getLogger(__name__)

OPENAI_CHAT_URL = "https://api.openai.com/v1/chat/completions"
MAX_TOKENS = 150
TEMPERATURE = 0.5


class LLMError(RuntimeError):
    """Raised when a provider call fails and the caller asked not to fall back."""


def _openai_payload(prompt: str, model: str, stream: bool) -> dict:
    return {
        "model": model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ],
        "temperature": TEMPERATURE,
        "max_tokens": MAX_TOKENS,
        "stream": stream,
    }


async def generate_response_gemini(
    user_query: str, retrieved_context: str, language: str, settings: Settings
) -> str:
    """Answer with Google Gemini, or the offline fallback when no key is configured."""
    if not settings.gemini_api_key:
        return mock_response(retrieved_context, language)

    def _call() -> str:
        from google import genai

        client = genai.Client(api_key=settings.gemini_api_key)
        response = client.models.generate_content(
            model=settings.gemini_model,
            contents=build_prompt(user_query, retrieved_context, language),
        )
        return (response.text or "").strip()

    try:
        text = await asyncio.to_thread(_call)
        return text or mock_response(retrieved_context, language)
    except Exception:
        logger.exception("Gemini request failed; using fallback response")
        return mock_response(retrieved_context, language)


async def generate_response_openai(
    user_query: str,
    retrieved_context: str,
    language: str,
    settings: Settings,
    client: httpx.AsyncClient | None = None,
) -> str:
    """Answer with a single (non-streaming) OpenAI chat completion."""
    if not settings.openai_api_key:
        logger.warning("OPENAI_API_KEY is not set; using fallback response")
        return mock_response(retrieved_context, language)

    prompt = build_prompt(user_query, retrieved_context, language)
    owns_client = client is None
    client = client or httpx.AsyncClient(timeout=settings.http_timeout_seconds)
    try:
        response = await client.post(
            OPENAI_CHAT_URL,
            headers={"Authorization": f"Bearer {settings.openai_api_key}"},
            json=_openai_payload(prompt, settings.openai_model, stream=False),
        )
        response.raise_for_status()
        content = response.json()["choices"][0]["message"]["content"]
        return (content or "").strip() or mock_response(retrieved_context, language)
    except (httpx.HTTPError, KeyError, IndexError, ValueError):
        logger.exception("OpenAI request failed; using fallback response")
        return mock_response(retrieved_context, language)
    finally:
        if owns_client:
            await client.aclose()


async def stream_openai_tokens(
    prompt: str,
    *,
    api_key: str,
    model: str,
    timeout: float = 30.0,
    client: httpx.AsyncClient | None = None,
) -> AsyncIterator[str]:
    """Yield content tokens from a streaming OpenAI chat completion.

    Raises :class:`LLMError` on transport or HTTP errors.
    """
    owns_client = client is None
    client = client or httpx.AsyncClient(timeout=timeout)
    try:
        async with client.stream(
            "POST",
            OPENAI_CHAT_URL,
            headers={"Authorization": f"Bearer {api_key}"},
            json=_openai_payload(prompt, model, stream=True),
        ) as response:
            if response.status_code >= 400:
                body = (await response.aread()).decode("utf-8", "replace")[:300]
                raise LLMError(f"OpenAI returned HTTP {response.status_code}: {body}")
            async for line in response.aiter_lines():
                if line.strip() == "data: [DONE]":
                    break
                token = parse_sse_delta(line)
                if token:
                    yield token
    except httpx.HTTPError as exc:
        raise LLMError(f"OpenAI request failed: {exc}") from exc
    finally:
        if owns_client:
            await client.aclose()


async def stream_response_openai(
    user_query: str,
    retrieved_context: str,
    language: str,
    settings: Settings,
    client: httpx.AsyncClient | None = None,
) -> AsyncIterator[str]:
    """Stream an answer token-by-token, falling back to the offline answer on failure."""
    if not settings.openai_api_key:
        logger.warning("OPENAI_API_KEY is not set; using fallback response")
        yield mock_response(retrieved_context, language)
        return

    emitted = False
    try:
        async for token in stream_openai_tokens(
            build_prompt(user_query, retrieved_context, language),
            api_key=settings.openai_api_key,
            model=settings.openai_model,
            timeout=settings.http_timeout_seconds,
            client=client,
        ):
            emitted = True
            yield token
    except LLMError:
        logger.exception("OpenAI streaming failed")
        if not emitted:
            yield mock_response(retrieved_context, language)
