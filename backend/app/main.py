"""FastAPI application: batch + streaming voice chat endpoints and the static frontend."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from contextlib import asynccontextmanager
from typing import Annotated, Any

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile, WebSocket
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, ValidationError
from starlette.websockets import WebSocketDisconnect, WebSocketState

from .config import Settings, get_settings
from .knowledge_base import DOCUMENTS, KnowledgeBase, fastembed_embedder
from .llm import (
    LLMError,
    generate_response_gemini,
    generate_response_openai,
    stream_openai_tokens,
    stream_response_openai,
)
from .stt import TranscriptionError, stream_transcription, transcribe_audio
from .text import SentenceChunker, build_prompt, encode_header_text
from .tts import PiperTTS

logger = logging.getLogger("voice_agent")

SUPPORTED_LANGUAGES = {"auto", "en", "hi", "mr", "ta", "te", "kn", "ml", "gu", "bn", "pa"}
BENCHMARK_CONTEXT = "This is a test context for benchmarking."


class StreamConfig(BaseModel):
    """First (JSON) message a client sends on ``/ws/chat``."""

    language: str = "en"
    use_llm: bool = False
    encoding: str | None = Field(default=None, pattern=r"^[a-z0-9_]{1,16}$")
    sample_rate: int | None = Field(default=None, ge=8000, le=48000)


class BenchmarkRequest(BaseModel):
    api_key: str | None = Field(default=None, max_length=256)
    models: list[Annotated[str, Field(pattern=r"^[A-Za-z0-9._:-]{1,64}$")]] = Field(
        min_length=1, max_length=10
    )
    prompt: str = Field(min_length=1, max_length=2000)


class BenchmarkResult(BaseModel):
    model: str
    ttft: float
    tps: float
    total_time: float
    error: str | None = None


def _normalize_language(language: str) -> str:
    language = (language or "en").strip().lower()
    return language if language in SUPPORTED_LANGUAGES else "en"


def create_app(
    settings: Settings | None = None,
    knowledge_base: KnowledgeBase | None = None,
    tts: PiperTTS | None = None,
    *,
    warm_up: bool = True,
) -> FastAPI:
    settings = settings or get_settings()
    kb = knowledge_base or KnowledgeBase(
        DOCUMENTS, lambda: fastembed_embedder(settings.embedding_model)
    )
    tts = tts or PiperTTS(settings.voices_dir)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        if warm_up:
            # Load models in the background so the server accepts requests immediately.
            async def _warm() -> None:
                try:
                    await asyncio.to_thread(kb.warm_up)
                    await asyncio.to_thread(tts.get_voice, "en")
                    logger.info("Models loaded")
                except Exception:
                    logger.exception("Model warm-up failed")

            task = asyncio.create_task(_warm())
            yield
            task.cancel()
        else:
            yield

    app = FastAPI(title="Voice AI Agent", version="1.0.0", lifespan=lifespan)
    app.state.settings = settings
    app.state.kb = kb
    app.state.tts = tts

    if settings.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.cors_origins,
            allow_methods=["GET", "POST"],
            allow_headers=["*"],
            expose_headers=["X-User-Transcript", "X-Agent-Transcript"],
        )

    @app.get("/health")
    async def health() -> dict[str, Any]:
        return {
            "status": "ok",
            "knowledge_base_ready": kb.is_ready,
            "providers": {
                "deepgram": bool(settings.deepgram_api_key),
                "openai": bool(settings.openai_api_key),
                "gemini": bool(settings.gemini_api_key),
            },
            "benchmark_enabled": settings.enable_benchmark,
        }

    async def answer(transcript: str, language: str, use_llm: bool) -> str:
        context = await asyncio.to_thread(kb.search, transcript, 2)
        if use_llm:
            return await generate_response_openai(transcript, context, language, settings)
        return await generate_response_gemini(transcript, context, language, settings)

    # ------------------------------------------------------------------ batch
    @app.post("/chat")
    async def chat(
        request: Request,
        audio: Annotated[UploadFile, File()],
        language: Annotated[str, Form()] = "en",
        use_llm: Annotated[bool, Form()] = False,
    ) -> Response:
        language = _normalize_language(language)
        audio_bytes = await audio.read(settings.max_upload_bytes + 1)
        if not audio_bytes:
            raise HTTPException(status_code=400, detail="Audio upload is empty")
        if len(audio_bytes) > settings.max_upload_bytes:
            raise HTTPException(status_code=413, detail="Audio upload is too large")

        if not settings.deepgram_api_key:
            raise HTTPException(status_code=503, detail="DEEPGRAM_API_KEY is not configured")

        content_type = (audio.content_type or "audio/webm").split(";")[0]
        try:
            transcript = await transcribe_audio(audio_bytes, language, settings, content_type)
        except TranscriptionError as exc:
            logger.warning("Transcription failed: %s", exc)
            raise HTTPException(status_code=502, detail=str(exc)) from exc

        if not transcript:
            raise HTTPException(status_code=422, detail="Could not understand the audio")

        agent_text = await answer(transcript, language, use_llm)
        try:
            wav = await tts.synthesize_wav(agent_text, language)
        except Exception as exc:
            logger.exception("Speech synthesis failed")
            raise HTTPException(status_code=500, detail="Speech synthesis failed") from exc

        return Response(
            content=wav,
            media_type="audio/wav",
            headers={
                "X-User-Transcript": encode_header_text(transcript),
                "X-Agent-Transcript": encode_header_text(agent_text),
                "Cache-Control": "no-store",
            },
        )

    # -------------------------------------------------------------- streaming
    @app.websocket("/ws/chat")
    async def websocket_chat(websocket: WebSocket) -> None:
        await websocket.accept()

        async def send_json(payload: dict[str, Any]) -> None:
            await websocket.send_text(json.dumps(payload, ensure_ascii=False))

        try:
            raw_config = await websocket.receive_text()
            try:
                config = StreamConfig.model_validate_json(raw_config)
            except ValidationError:
                await send_json({"type": "error", "message": "Invalid stream configuration"})
                return
            language = _normalize_language(config.language)

            async def receive_audio() -> bytes | None:
                message = await websocket.receive()
                if message["type"] == "websocket.disconnect":
                    return None
                return message.get("bytes") or None

            try:
                transcript = await stream_transcription(
                    receive_audio, language, settings, config.encoding, config.sample_rate
                )
            except TranscriptionError as exc:
                logger.warning("Streaming transcription failed: %s", exc)
                await send_json({"type": "error", "message": str(exc)})
                return

            if not transcript:
                await send_json({"type": "error", "message": "No speech detected"})
                return

            await send_json({"type": "transcript", "speaker": "user", "text": transcript})
            started = time.perf_counter()
            context = await asyncio.to_thread(kb.search, transcript, 2)

            if config.use_llm:
                sentences = _stream_sentences(
                    stream_response_openai(transcript, context, language, settings),
                    send_json,
                )
            else:
                agent_text = await generate_response_gemini(transcript, context, language, settings)
                await send_json(
                    {"type": "transcript", "speaker": "agent", "text": agent_text, "final": True}
                )
                sentences = _single(agent_text)

            await send_json({"type": "audio_start"})
            await tts.stream(sentences, language, websocket.send_bytes)
            await send_json({"type": "audio_end"})
            logger.info("Streamed response in %.2fs", time.perf_counter() - started)
        except WebSocketDisconnect:
            logger.info("Client disconnected")
        except Exception:
            logger.exception("Unexpected error in /ws/chat")
            if websocket.client_state == WebSocketState.CONNECTED:
                await send_json({"type": "error", "message": "Internal server error"})
        finally:
            if websocket.client_state == WebSocketState.CONNECTED:
                await websocket.close()

    # -------------------------------------------------------------- benchmark
    @app.post("/benchmark_stream", response_model=list[BenchmarkResult])
    async def benchmark_stream(req: BenchmarkRequest) -> list[BenchmarkResult]:
        if not settings.enable_benchmark:
            raise HTTPException(status_code=404, detail="Benchmark is disabled")
        api_key = (req.api_key or "").strip() or None
        if api_key is None and settings.benchmark_allow_server_key:
            api_key = settings.openai_api_key
        if not api_key:
            raise HTTPException(status_code=400, detail="An OpenAI API key is required")

        prompt = build_prompt(req.prompt, BENCHMARK_CONTEXT, "en")
        results: list[BenchmarkResult] = []
        for model in dict.fromkeys(req.models):  # de-duplicate, keep order
            results.append(await _benchmark_model(prompt, model, api_key, settings))
        results.sort(key=lambda r: (r.error is not None, -r.tps))
        return results

    if settings.frontend_dir.is_dir():
        app.mount("/", StaticFiles(directory=settings.frontend_dir, html=True), name="frontend")
    else:
        logger.warning("Frontend directory %s not found; UI disabled", settings.frontend_dir)

    @app.exception_handler(HTTPException)
    async def http_error(_: Request, exc: HTTPException) -> JSONResponse:
        return JSONResponse({"error": exc.detail}, status_code=exc.status_code)

    @app.exception_handler(RequestValidationError)
    async def validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
        first = exc.errors()[0] if exc.errors() else {}
        where = ".".join(str(part) for part in first.get("loc", ()) if part != "body")
        message = f"Invalid request: {where}: {first.get('msg', 'validation failed')}"
        return JSONResponse({"error": message, "detail": exc.errors()}, status_code=422)

    return app


async def _stream_sentences(tokens, send_json):
    """Turn an LLM token stream into sentences, pushing transcript updates as it goes."""
    chunker = SentenceChunker()
    full_text = ""
    async for token in tokens:
        full_text += token
        for sentence in chunker.feed(token):
            await send_json({"type": "transcript", "speaker": "agent", "text": full_text.strip()})
            yield sentence
    tail = chunker.flush()
    await send_json(
        {"type": "transcript", "speaker": "agent", "text": full_text.strip(), "final": True}
    )
    if tail:
        yield tail


async def _single(text: str):
    yield text


async def _benchmark_model(
    prompt: str, model: str, api_key: str, settings: Settings
) -> BenchmarkResult:
    start = time.perf_counter()
    ttft: float | None = None
    chunks = 0
    try:
        async for token in stream_openai_tokens(
            prompt, api_key=api_key, model=model, timeout=settings.http_timeout_seconds
        ):
            if ttft is None and token.strip():
                ttft = time.perf_counter() - start
            chunks += 1
    except LLMError as exc:
        return BenchmarkResult(model=model, ttft=0, tps=0, total_time=0, error=str(exc))
    total = time.perf_counter() - start
    return BenchmarkResult(
        model=model,
        ttft=ttft or 0.0,
        tps=chunks / total if total > 0 else 0.0,
        total_time=total,
    )


def _configure_logging() -> None:
    if not logging.getLogger().handlers:
        logging.basicConfig(
            level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
        )


_configure_logging()
app = create_app()

__all__ = ["app", "create_app"]
