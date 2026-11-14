"""Manual end-to-end check of the batch ``POST /chat`` endpoint.

Requires a running server with ``DEEPGRAM_API_KEY`` configured.

    python scripts/smoke_batch.py path/to/audio.wav [--url http://localhost:8000] [--language en]
"""

from __future__ import annotations

import argparse
import mimetypes
import sys
import time
from pathlib import Path

import httpx


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("audio", type=Path, help="Audio file to send (wav, webm, mp3, ...)")
    parser.add_argument("--url", default="http://localhost:8000")
    parser.add_argument("--language", default="en")
    parser.add_argument("--use-llm", action="store_true", help="Use OpenAI instead of Gemini")
    parser.add_argument("--out", type=Path, default=Path("response.wav"))
    args = parser.parse_args()

    content_type = mimetypes.guess_type(args.audio.name)[0] or "application/octet-stream"
    files = {"audio": (args.audio.name, args.audio.read_bytes(), content_type)}
    data = {"language": args.language, "use_llm": str(args.use_llm).lower()}

    start = time.perf_counter()
    response = httpx.post(f"{args.url}/chat", files=files, data=data, timeout=120)
    latency = time.perf_counter() - start

    print(f"Status: {response.status_code}  latency: {latency:.2f}s")
    if response.status_code != 200:
        print(response.text)
        return 1

    def decode(name: str) -> str:
        value = response.headers.get(name, "")
        return bytes.fromhex(value).decode("utf-8") if value else "N/A"

    print(f"User:  {decode('X-User-Transcript')}")
    print(f"Agent: {decode('X-Agent-Transcript')}")
    args.out.write_bytes(response.content)
    print(f"Wrote {len(response.content)} bytes of audio to {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
