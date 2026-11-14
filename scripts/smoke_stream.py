"""Manual end-to-end latency check of the streaming ``/ws/chat`` endpoint.

Requires a running server with ``DEEPGRAM_API_KEY`` configured (and
``OPENAI_API_KEY`` for ``--use-llm``).

    python scripts/smoke_stream.py path/to/audio.wav [--url ws://localhost:8000] [--use-llm]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

import websockets

CHUNK_SIZE = 4096


async def run(audio: bytes, url: str, language: str, use_llm: bool) -> int:
    async with websockets.connect(f"{url}/ws/chat", max_size=None) as ws:
        await ws.send(json.dumps({"language": language, "use_llm": use_llm}))
        start = time.perf_counter()

        async def sender() -> None:
            for i in range(0, len(audio), CHUNK_SIZE):
                await ws.send(audio[i : i + CHUNK_SIZE])
                await asyncio.sleep(0.01)
            await ws.send(b"")  # end of audio
            print("Finished sending audio, waiting for the agent...")

        async def receiver() -> int:
            stt_done: float | None = None
            audio_bytes = 0
            async for message in ws:
                if isinstance(message, bytes):
                    if audio_bytes == 0 and stt_done is not None:
                        ttfb = (time.perf_counter() - stt_done) * 1000
                        print(f"--> transcript to first audio byte: {ttfb:.0f} ms")
                    audio_bytes += len(message)
                    continue
                event = json.loads(message)
                kind = event.get("type")
                if kind == "transcript":
                    if event["speaker"] == "user":
                        stt_done = time.perf_counter()
                    print(f"[{event['speaker']}] {event['text']}")
                elif kind == "error":
                    print(f"Error: {event['message']}")
                    return 1
                elif kind == "audio_end":
                    total = time.perf_counter() - start
                    print(f"Received {audio_bytes} audio bytes; total {total:.2f}s")
                    return 0
            return 1

        _, code = await asyncio.gather(sender(), receiver())
        return code


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("audio", type=Path)
    parser.add_argument("--url", default="ws://localhost:8000")
    parser.add_argument("--language", default="en")
    parser.add_argument("--use-llm", action="store_true")
    args = parser.parse_args()
    return asyncio.run(run(args.audio.read_bytes(), args.url, args.language, args.use_llm))


if __name__ == "__main__":
    sys.exit(main())
