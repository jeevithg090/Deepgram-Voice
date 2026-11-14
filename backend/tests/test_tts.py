import io
import struct
import wave

import pytest

from app.config import BACKEND_DIR
from app.tts import PiperTTS, pcm_to_wav, streaming_wav_header, voice_language


def test_voice_language_falls_back_to_english():
    assert voice_language("hi") == "hi"
    assert voice_language("ta") == "en"


def test_streaming_wav_header_layout():
    header = streaming_wav_header(22050)
    assert len(header) == 44
    assert header[:4] == b"RIFF" and header[8:12] == b"WAVE" and header[36:40] == b"data"
    fmt, channels, rate, byte_rate, align, bits = struct.unpack("<HHIIHH", header[20:36])
    assert (fmt, channels, rate, byte_rate, align, bits) == (1, 1, 22050, 44100, 2, 16)


def test_pcm_to_wav_is_readable():
    wav = pcm_to_wav(b"\x00\x00" * 100, 16000)
    with wave.open(io.BytesIO(wav)) as f:
        assert f.getframerate() == 16000
        assert f.getnframes() == 100


def test_missing_voice_file_raises(tmp_path):
    pytest.importorskip("piper")
    with pytest.raises(FileNotFoundError):
        PiperTTS(tmp_path).get_voice("en")


@pytest.mark.slow
async def test_real_piper_voice_synthesizes_audio():
    pytest.importorskip("piper")
    voices = BACKEND_DIR / "voices"
    if not (voices / "en_US-lessac-medium.onnx").exists():
        pytest.skip("voice model not available")
    tts = PiperTTS(voices)
    wav = await tts.synthesize_wav("Hello there.", "en")
    with wave.open(io.BytesIO(wav)) as f:
        assert f.getnframes() > 1000

    chunks: list[bytes] = []

    async def sentences():
        yield "One."
        yield "Two."

    async def send(data: bytes) -> None:
        chunks.append(data)

    await tts.stream(sentences(), "en", send)
    assert len(chunks[0]) == 44 and len(chunks) == 3
