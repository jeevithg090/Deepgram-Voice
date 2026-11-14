"""Local text-to-speech with Piper ONNX voices."""

from __future__ import annotations

import asyncio
import io
import logging
import struct
import threading
import wave
from collections.abc import AsyncIterator, Awaitable, Callable
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Language code -> Piper voice file. Languages without a dedicated voice use English.
VOICE_FILES: dict[str, str] = {
    "en": "en_US-lessac-medium.onnx",
    "hi": "hi_IN-rohan-medium.onnx",
}
DEFAULT_VOICE_LANGUAGE = "en"

# Size field used for streamed WAV files whose final length is unknown.
_UNKNOWN_SIZE = 0xFFFFFFFF


def voice_language(language: str) -> str:
    return language if language in VOICE_FILES else DEFAULT_VOICE_LANGUAGE


def streaming_wav_header(sample_rate: int, channels: int = 1, sample_width: int = 2) -> bytes:
    """Build a 44-byte PCM WAV header suitable for an open-ended stream."""
    byte_rate = sample_rate * channels * sample_width
    block_align = channels * sample_width
    return (
        b"RIFF"
        + struct.pack("<I", _UNKNOWN_SIZE)
        + b"WAVE"
        + b"fmt "
        + struct.pack("<IHHIIHH", 16, 1, channels, sample_rate, byte_rate, block_align, 16)
        + b"data"
        + struct.pack("<I", _UNKNOWN_SIZE)
    )


def pcm_to_wav(pcm: bytes, sample_rate: int) -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(sample_rate)
        wav_file.writeframes(pcm)
    return buffer.getvalue()


class PiperTTS:
    """Lazily loads Piper voices and synthesizes speech in a worker thread."""

    def __init__(self, voices_dir: Path):
        self.voices_dir = voices_dir
        self._voices: dict[str, Any] = {}
        self._lock = threading.Lock()

    def get_voice(self, language: str) -> Any:
        lang = voice_language(language)
        with self._lock:
            if lang not in self._voices:
                from piper import PiperVoice

                path = self.voices_dir / VOICE_FILES[lang]
                if not path.exists():
                    raise FileNotFoundError(f"Piper voice model not found: {path}")
                logger.info("Loading Piper voice %s", path.name)
                self._voices[lang] = PiperVoice.load(str(path))
            return self._voices[lang]

    def _synthesize_pcm(self, text: str, language: str) -> tuple[bytes, int]:
        voice = self.get_voice(language)
        pcm = b"".join(chunk.audio_int16_bytes for chunk in voice.synthesize(text))
        return pcm, voice.config.sample_rate

    async def synthesize_wav(self, text: str, language: str) -> bytes:
        """Synthesize a complete WAV file for ``text``."""
        pcm, sample_rate = await asyncio.to_thread(self._synthesize_pcm, text, language)
        return pcm_to_wav(pcm, sample_rate)

    async def stream(
        self,
        sentences: AsyncIterator[str],
        language: str,
        send_bytes: Callable[[bytes], Awaitable[None]],
    ) -> None:
        """Synthesize each sentence as it arrives and send WAV bytes incrementally.

        The first message is a streaming WAV header; subsequent messages are raw
        16-bit PCM frames.
        """
        voice = await asyncio.to_thread(self.get_voice, language)
        await send_bytes(streaming_wav_header(voice.config.sample_rate))

        def _synth(sentence: str) -> bytes:
            return b"".join(chunk.audio_int16_bytes for chunk in voice.synthesize(sentence))

        # Drain the sentence source in a separate task so the language model keeps
        # generating while earlier sentences are being synthesized.
        queue: asyncio.Queue[str | None] = asyncio.Queue()

        async def produce() -> None:
            try:
                async for sentence in sentences:
                    if sentence.strip():
                        await queue.put(sentence)
            finally:
                await queue.put(None)

        producer = asyncio.create_task(produce())
        try:
            while (sentence := await queue.get()) is not None:
                pcm = await asyncio.to_thread(_synth, sentence)
                if pcm:
                    await send_bytes(pcm)
            await producer  # surface producer exceptions
        finally:
            if not producer.done():
                producer.cancel()
