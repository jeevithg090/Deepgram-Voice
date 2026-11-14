import json
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

import app.main as main_module
from app.llm import LLMError
from app.main import create_app
from app.stt import TranscriptionError
from app.text import decode_header_text


@pytest.fixture
def fake_stt(monkeypatch):
    state = {"transcript": "what is your return policy", "error": None, "chunks": []}

    async def transcribe(audio, language, settings, content_type="audio/webm", client=None):
        if state["error"]:
            raise TranscriptionError(state["error"])
        state["language"] = language
        return state["transcript"]

    async def stream(receive_audio, language, settings, encoding=None, sample_rate=None):
        if state["error"]:
            raise TranscriptionError(state["error"])
        while chunk := await receive_audio():
            state["chunks"].append(chunk)
        return state["transcript"]

    monkeypatch.setattr(main_module, "transcribe_audio", transcribe)
    monkeypatch.setattr(main_module, "stream_transcription", stream)
    return state


def test_health_reports_configured_providers(client):
    body = client.get("/health").json()
    assert body["status"] == "ok"
    assert body["providers"] == {"deepgram": True, "openai": False, "gemini": False}


def test_frontend_is_served(client):
    assert "<title>t</title>" in client.get("/").text


def test_batch_chat_returns_audio_and_transcripts(client, fake_stt, fake_tts):
    response = client.post(
        "/chat",
        files={"audio": ("a.webm", b"fake-audio", "audio/webm")},
        data={"language": "en"},
    )
    assert response.status_code == 200
    assert response.headers["content-type"] == "audio/wav"
    assert decode_header_text(response.headers["x-user-transcript"]) == fake_stt["transcript"]
    agent = decode_header_text(response.headers["x-agent-transcript"])
    assert "return policy" in agent  # offline fallback answers from the knowledge base
    assert fake_tts.spoken == [agent]


def test_batch_chat_normalizes_unknown_language(client, fake_stt):
    client.post("/chat", files={"audio": ("a.webm", b"x", "audio/webm")}, data={"language": "zz"})
    assert fake_stt["language"] == "en"


def test_batch_chat_rejects_empty_upload(client, fake_stt):
    response = client.post("/chat", files={"audio": ("a.webm", b"", "audio/webm")})
    assert response.status_code == 400
    assert response.json() == {"error": "Audio upload is empty"}


def test_batch_chat_rejects_oversized_upload(client, fake_stt, settings):
    big = b"x" * (settings.max_upload_bytes + 1)
    response = client.post("/chat", files={"audio": ("a.webm", big, "audio/webm")})
    assert response.status_code == 413


def test_batch_chat_reports_unintelligible_audio(client, fake_stt):
    fake_stt["transcript"] = ""
    response = client.post("/chat", files={"audio": ("a.webm", b"x", "audio/webm")})
    assert response.status_code == 422


def test_batch_chat_requires_deepgram_key(settings, kb, fake_tts, fake_stt):
    app = create_app(replace(settings, deepgram_api_key=None), kb, fake_tts, warm_up=False)
    with TestClient(app) as no_key_client:
        response = no_key_client.post("/chat", files={"audio": ("a.webm", b"x", "audio/webm")})
    assert response.status_code == 503


def test_batch_chat_reports_stt_failure(client, fake_stt):
    fake_stt["error"] = "Deepgram returned HTTP 401"
    response = client.post("/chat", files={"audio": ("a.webm", b"x", "audio/webm")})
    assert response.status_code == 502
    assert "401" in response.json()["error"]


def receive_all(ws):
    events, audio = [], []
    while True:
        message = ws.receive()
        if message["type"] == "websocket.close":
            return events, audio
        if message.get("text") is not None:
            events.append(json.loads(message["text"]))
        elif message.get("bytes") is not None:
            audio.append(message["bytes"])


def test_websocket_streams_transcripts_and_audio(client, fake_stt):
    with client.websocket_connect("/ws/chat") as ws:
        ws.send_text(json.dumps({"language": "en", "use_llm": False}))
        ws.send_bytes(b"chunk-1")
        ws.send_bytes(b"chunk-2")
        ws.send_bytes(b"")
        events, audio = receive_all(ws)

    assert fake_stt["chunks"] == [b"chunk-1", b"chunk-2"]
    types = [e["type"] for e in events]
    assert types == ["transcript", "transcript", "audio_start", "audio_end"]
    assert events[0] == {"type": "transcript", "speaker": "user", "text": fake_stt["transcript"]}
    assert events[1]["speaker"] == "agent" and events[1]["final"] is True
    assert len(audio[0]) == 44  # WAV header first


def test_websocket_llm_mode_streams_sentences(client, fake_stt, fake_tts, monkeypatch):
    async def fake_stream(query, context, language, settings, client=None):
        for token in ["Returns are ", "free. ", "Ship", "ping too."]:
            yield token

    monkeypatch.setattr(main_module, "stream_response_openai", fake_stream)
    with client.websocket_connect("/ws/chat") as ws:
        ws.send_text(json.dumps({"language": "en", "use_llm": True}))
        ws.send_bytes(b"")
        events, _ = receive_all(ws)

    agent = [e for e in events if e.get("speaker") == "agent"]
    assert agent[-1] == {
        "type": "transcript",
        "speaker": "agent",
        "text": "Returns are free. Shipping too.",
        "final": True,
    }
    assert fake_tts.spoken == ["Returns are free.", "Shipping too."]


def test_websocket_reports_no_speech(client, fake_stt):
    fake_stt["transcript"] = ""
    with client.websocket_connect("/ws/chat") as ws:
        ws.send_text(json.dumps({"language": "en"}))
        ws.send_bytes(b"")
        events, _ = receive_all(ws)
    assert events == [{"type": "error", "message": "No speech detected"}]


def test_websocket_reports_stt_error(client, fake_stt):
    fake_stt["error"] = "DEEPGRAM_API_KEY is not configured on the server"
    with client.websocket_connect("/ws/chat") as ws:
        ws.send_text(json.dumps({"language": "en"}))
        events, _ = receive_all(ws)
    assert events[0]["type"] == "error"
    assert "DEEPGRAM_API_KEY" in events[0]["message"]


def test_websocket_rejects_invalid_config(client):
    with client.websocket_connect("/ws/chat") as ws:
        ws.send_text("not json")
        events, _ = receive_all(ws)
    assert events == [{"type": "error", "message": "Invalid stream configuration"}]


def test_benchmark_requires_api_key(client):
    response = client.post("/benchmark_stream", json={"models": ["gpt-4o-mini"], "prompt": "hi"})
    assert response.status_code == 400


def test_benchmark_validates_model_names(client):
    response = client.post(
        "/benchmark_stream", json={"api_key": "k", "models": ["bad model!"], "prompt": "hi"}
    )
    assert response.status_code == 422


def test_benchmark_ranks_models_and_reports_errors(client, monkeypatch):
    async def fake_tokens(prompt, *, api_key, model, timeout=30.0, client=None):
        if model == "broken":
            raise LLMError("OpenAI returned HTTP 404")
        for _ in range(50 if model == "fast" else 5):
            yield "tok"

    monkeypatch.setattr(main_module, "stream_openai_tokens", fake_tokens)
    response = client.post(
        "/benchmark_stream",
        json={"api_key": "k", "models": ["slow", "broken", "fast", "fast"], "prompt": "hi"},
    )
    assert response.status_code == 200
    rows = response.json()
    assert [r["model"] for r in rows] == ["fast", "slow", "broken"]
    assert rows[2]["error"] == "OpenAI returned HTTP 404"
    assert rows[0]["tps"] > rows[1]["tps"]
