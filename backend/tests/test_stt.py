from dataclasses import replace
from urllib.parse import parse_qs

import httpx
import pytest

from app.stt import TranscriptionError, build_listen_query, extract_transcript, transcribe_audio


def q(query: str) -> dict[str, str]:
    return {k: v[0] for k, v in parse_qs(query).items()}


def test_batch_auto_language_uses_detection():
    params = q(build_listen_query("nova-2", "auto", streaming=False))
    assert params["detect_language"] == "true"
    assert "language" not in params


def test_streaming_auto_language_falls_back_to_english():
    params = q(build_listen_query("nova-2", "auto", streaming=True))
    assert params["language"] == "en"
    assert "detect_language" not in params


def test_streaming_includes_encoding_and_sample_rate():
    params = q(
        build_listen_query("nova-2", "hi", streaming=True, encoding="linear16", sample_rate=16000)
    )
    assert params == {
        "model": "nova-2",
        "smart_format": "true",
        "language": "hi",
        "encoding": "linear16",
        "sample_rate": "16000",
    }


def test_extract_transcript_from_rest_and_streaming_payloads():
    rest = {"results": {"channels": [{"alternatives": [{"transcript": " hello "}]}]}}
    stream = {"channel": {"alternatives": [{"transcript": "world"}]}}
    assert extract_transcript(rest) == "hello"
    assert extract_transcript(stream) == "world"
    assert extract_transcript({}) == ""


async def test_transcribe_audio_sends_key_and_content_type(settings):
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["auth"] = request.headers["authorization"]
        seen["type"] = request.headers["content-type"]
        seen["body"] = request.content
        return httpx.Response(
            200, json={"results": {"channels": [{"alternatives": [{"transcript": "hi"}]}]}}
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        text = await transcribe_audio(b"abc", "en", settings, "audio/ogg", client=client)

    assert text == "hi"
    assert seen == {"auth": "Token dg-test", "type": "audio/ogg", "body": b"abc"}


async def test_transcribe_audio_raises_on_http_error(settings):
    transport = httpx.MockTransport(lambda r: httpx.Response(401, json={"err": "bad key"}))
    async with httpx.AsyncClient(transport=transport) as client:
        with pytest.raises(TranscriptionError, match="401"):
            await transcribe_audio(b"abc", "en", settings, client=client)


async def test_transcribe_audio_requires_api_key(settings):
    with pytest.raises(TranscriptionError, match="DEEPGRAM_API_KEY"):
        await transcribe_audio(b"abc", "en", replace(settings, deepgram_api_key=None))
