import json
from dataclasses import replace

import httpx
import pytest

from app.llm import (
    LLMError,
    generate_response_gemini,
    generate_response_openai,
    stream_openai_tokens,
    stream_response_openai,
)


def sse(*tokens: str) -> bytes:
    lines = [
        "data: " + json.dumps({"choices": [{"delta": {"content": t}}]}) + "\n\n" for t in tokens
    ]
    return ("".join(lines) + "data: [DONE]\n\n").encode()


@pytest.fixture
def openai_settings(settings):
    return replace(settings, openai_api_key="sk-test", openai_model="test-model")


async def collect(agen):
    return [item async for item in agen]


async def test_stream_tokens_parses_sse_and_sends_model(openai_settings):
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(json.loads(request.content))
        seen["auth"] = request.headers["authorization"]
        return httpx.Response(200, content=sse("Hello", " world", "."))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        tokens = await collect(
            stream_openai_tokens("hi", api_key="sk-test", model="m1", client=client)
        )

    assert tokens == ["Hello", " world", "."]
    assert seen["model"] == "m1"
    assert seen["stream"] is True
    assert seen["auth"] == "Bearer sk-test"


async def test_stream_tokens_raises_on_http_error():
    transport = httpx.MockTransport(lambda r: httpx.Response(429, text="rate limited"))
    async with httpx.AsyncClient(transport=transport) as client:
        with pytest.raises(LLMError, match="429"):
            await collect(stream_openai_tokens("hi", api_key="k", model="m", client=client))


async def test_stream_response_falls_back_without_key(settings):
    tokens = await collect(stream_response_openai("q", "Ctx line.", "en", settings))
    assert tokens == ["Based on our records: Ctx line."]


async def test_stream_response_falls_back_on_provider_error(openai_settings):
    transport = httpx.MockTransport(lambda r: httpx.Response(500, text="boom"))
    async with httpx.AsyncClient(transport=transport) as client:
        tokens = await collect(
            stream_response_openai("q", "Ctx.", "en", openai_settings, client=client)
        )
    assert tokens == ["Based on our records: Ctx."]


async def test_generate_openai_returns_message_content(openai_settings):
    body = {"choices": [{"message": {"content": "  Ships in 3 days. "}}]}
    transport = httpx.MockTransport(lambda r: httpx.Response(200, json=body))
    async with httpx.AsyncClient(transport=transport) as client:
        text = await generate_response_openai("q", "ctx", "en", openai_settings, client=client)
    assert text == "Ships in 3 days."


async def test_generate_openai_falls_back_on_malformed_body(openai_settings):
    transport = httpx.MockTransport(lambda r: httpx.Response(200, json={"oops": True}))
    async with httpx.AsyncClient(transport=transport) as client:
        text = await generate_response_openai("q", "Fact.", "en", openai_settings, client=client)
    assert text == "Based on our records: Fact."


async def test_gemini_without_key_uses_fallback(settings):
    assert await generate_response_gemini("q", "Fact.", "hi", settings) == (
        "हमारे रिकॉर्ड के अनुसार: Fact."
    )
