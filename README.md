# Voice AI Agent

A real-time, full-stack voice assistant for customer support. You hold a button, speak, and the agent
answers out loud. Your speech is transcribed by **Deepgram**, the agent looks up relevant facts in a
small **RAG knowledge base**, writes an answer with **OpenAI** or **Google Gemini**, and speaks it
with **Piper**, a text-to-speech engine that runs locally.

The demo knowledge base is a mock e-commerce FAQ ("MockStore": shipping, returns, payments,
orders). It has three processing modes, so you can compare request/response latency with
end-to-end streaming.

## Features

- **Three conversation modes**
  - **Batch:** record a clip, upload it, get back a WAV answer (Deepgram REST).
  - **Streaming:** microphone audio is streamed over a WebSocket to Deepgram's live API while you speak.
  - **Streaming LLM:** like Streaming, but OpenAI tokens are split into sentences and each sentence
    is synthesized and played as soon as it is ready. You start hearing the answer before the model
    has finished writing it.
- **Retrieval-augmented answers:** FastEmbed (`BAAI/bge-small-en-v1.5`, ONNX, CPU) ranks the FAQ
  entries by cosine similarity and the top matches are injected into the prompt.
- **Local, multilingual TTS:** Piper voices for English and Hindi are bundled. Other languages fall
  back to the English voice.
- **Graceful degradation:** with no LLM key, or when a provider fails, the agent answers with the
  best knowledge-base match, so the pipeline still works with only a Deepgram key.
- **11 speech languages:** English, Hindi, Marathi, Tamil, Telugu, Kannada, Malayalam, Gujarati,
  Bengali and Punjabi, plus auto-detection for batch mode.
- **Model benchmark page** (`/report.html`): measures time-to-first-token and streaming throughput
  for a set of OpenAI models.
- **Accessible, responsive UI:** press-and-hold works with mouse, touch and keyboard (Space/Enter).
  The chat log and status messages are announced to screen readers.

## Tech stack

| Layer          | Technology                                                                |
| -------------- | ------------------------------------------------------------------------- |
| Backend        | Python 3.10+, FastAPI, Uvicorn, httpx, websockets                         |
| Speech-to-text | Deepgram `nova-2` (REST + live WebSocket)                                 |
| Retrieval      | FastEmbed + NumPy cosine similarity                                       |
| LLM            | OpenAI Chat Completions (streaming) / Google Gemini (`google-genai`)      |
| Text-to-speech | Piper (`piper-tts`, ONNX voices bundled in `backend/voices/`)             |
| Frontend       | Plain HTML/CSS/JavaScript with MediaRecorder and Web Audio, no build step |
| Tooling        | pytest, Ruff (lint + format), ESLint, Prettier, GitHub Actions            |

## Architecture

```
Browser (frontend/)                         FastAPI server (backend/app/)
───────────────────                         ──────────────────────────────────────────────
MediaRecorder ──── POST /chat (clip) ─────▶ stt.transcribe_audio ──▶ Deepgram REST
                                            knowledge_base.search  (FastEmbed, local)
                                            llm.generate_response_* ──▶ Gemini / OpenAI
             ◀──── audio/wav + transcripts  tts.synthesize_wav      (Piper, local)
                   (hex-encoded headers)

MediaRecorder ──── WS /ws/chat ───────────▶ stt.stream_transcription ──▶ Deepgram live WS
  1. {"language","use_llm"} JSON            knowledge_base.search
  2. binary audio chunks                    llm.stream_response_openai ─▶ token stream
  3. empty frame = end of speech            text.SentenceChunker ─▶ sentences
Web Audio    ◀──── JSON events + WAV header tts.stream (Piper, worker thread per sentence)
player             then raw PCM chunks
```

**WebSocket events (server → client):** `transcript` (`speaker: "user" | "agent"`, the agent text
grows as it streams, and `final: true` marks the complete text), `audio_start`, binary audio (a
44-byte streaming WAV header followed by 16-bit mono PCM), `audio_end`, and `error` (`message`).

## Getting started

### Prerequisites

- Python **3.10+** (developed and tested on 3.12)
- A [Deepgram API key](https://console.deepgram.com/) (required for speech-to-text)
- Optional: an OpenAI and/or Gemini API key for generated answers
- Node.js 18+ only if you want to run the frontend linters

On first use the server downloads the FastEmbed embedding model (~70 MB) from Hugging Face and
caches it locally.

### Install

```bash
git clone https://github.com/jeevithg090/Deepgram-Voice.git
cd Deepgram-Voice

python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r backend/requirements-dev.txt   # or requirements.txt for runtime only

cp .env.example .env               # then fill in your keys
```

### Run

```bash
cd backend
uvicorn app.main:app --reload --port 8000
```

Open <http://localhost:8000>, allow microphone access, pick a mode and language, then hold the
button and speak. Browsers only allow microphone access on `localhost` or over HTTPS.

Useful endpoints:

| Method | Path                | Purpose                                                  |
| ------ | ------------------- | -------------------------------------------------------- |
| GET    | `/health`           | Liveness, knowledge-base readiness, configured providers |
| POST   | `/chat`             | Batch: multipart `audio`, `language`, `use_llm`          |
| WS     | `/ws/chat`          | Streaming conversation (protocol above)                  |
| POST   | `/benchmark_stream` | `{api_key?, models[], prompt}` → ranked results          |
| GET    | `/docs`             | Interactive OpenAPI docs                                 |

## Environment variables

All settings are read from the environment or from a `.env` file in the project root or in
`backend/`. See [`.env.example`](.env.example).

| Variable                     | Default                  | Description                                                                         |
| ---------------------------- | ------------------------ | ----------------------------------------------------------------------------------- |
| `DEEPGRAM_API_KEY`           | none                     | **Required** for speech-to-text.                                                    |
| `OPENAI_API_KEY`             | none                     | Enables generated answers in *Streaming LLM* mode.                                  |
| `GEMINI_API_KEY`             | none                     | Enables generated answers in *Batch* and *Streaming* modes.                         |
| `DEEPGRAM_MODEL`             | `nova-2`                 | Deepgram model.                                                                     |
| `OPENAI_MODEL`               | `gpt-4o-mini`            | OpenAI chat model.                                                                  |
| `GEMINI_MODEL`               | `gemini-2.5-flash`       | Gemini model.                                                                       |
| `EMBEDDING_MODEL`            | `BAAI/bge-small-en-v1.5` | FastEmbed model used for retrieval.                                                 |
| `CORS_ORIGINS`               | empty                    | Comma-separated cross-origin allow list. Empty means same-origin only.              |
| `MAX_UPLOAD_BYTES`           | `10485760`               | Upload size limit for `/chat`.                                                      |
| `HTTP_TIMEOUT_SECONDS`       | `30`                     | Timeout for outbound provider calls.                                                |
| `ENABLE_BENCHMARK`           | `true`                   | Turns the `/benchmark_stream` endpoint on or off.                                   |
| `BENCHMARK_ALLOW_SERVER_KEY` | `false`                  | Lets the benchmark use `OPENAI_API_KEY` when the visitor gives no key. Keep this off on public servers. |
| `FRONTEND_DIR`, `VOICES_DIR` | repo paths               | Override the static-files and Piper-voice locations.                               |

## Testing and quality checks

```bash
# Backend: run from the repository root
pytest                 # 58 tests: text utils, retrieval, STT/LLM clients (mocked HTTP), API + WebSocket, Piper
ruff check .           # lint
ruff format --check .  # formatting

# Frontend
npm ci
npm run lint           # ESLint
npm run format:check   # Prettier
```

The test suite needs no API keys and no network access. HTTP providers are mocked with
`httpx.MockTransport`, and the knowledge base uses a deterministic hashing embedder. One test
loads the bundled English Piper voice to check real synthesis.

For manual end-to-end checks against a running server with real keys, use the scripts in
`scripts/`:

```bash
python scripts/smoke_batch.py sample.wav --language en
python scripts/smoke_stream.py sample.wav --use-llm
OPENAI_API_KEY=... python scripts/run_benchmark.py --models gpt-4o-mini gpt-4o
```

GitHub Actions (`.github/workflows/ci.yml`) runs the backend lint and tests and the frontend lint
on every push and pull request.

## Deployment

The app is one ASGI service that also serves the static frontend, so it deploys anywhere that runs
Python and supports WebSockets (a VM, Render, Railway, Fly.io, a container platform and so on).

```bash
pip install -r backend/requirements.txt
cd backend
uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000} --workers 1
```

Production notes:

- **Serve over HTTPS.** Browsers block microphone access on insecure origins. Your reverse proxy
  must also forward WebSocket upgrades for `/ws/chat`.
- **Memory:** each worker loads the embedding model and the Piper voices (~120 MB of ONNX). Start
  with one worker per CPU core you can spare.
- **Warm-up:** models load in the background at startup. `/health` reports `knowledge_base_ready`.
  Pre-download the FastEmbed model at build time to avoid a cold-start fetch.
- **Secrets:** set API keys as environment variables or platform secrets, never in the repository.
  Leave `BENCHMARK_ALLOW_SERVER_KEY=false` (or set `ENABLE_BENCHMARK=false`) on public servers.

## Project structure

```
.
├── backend/
│   ├── app/
│   │   ├── main.py            # FastAPI app factory, routes (/chat, /ws/chat, /benchmark_stream, /health)
│   │   ├── config.py          # Environment-driven settings (.env support)
│   │   ├── stt.py             # Deepgram REST + live WebSocket client
│   │   ├── llm.py             # OpenAI (streaming / non-streaming) and Gemini clients with fallbacks
│   │   ├── knowledge_base.py  # FAQ documents + lazy FastEmbed cosine-similarity search
│   │   ├── tts.py             # Piper voice loading, WAV helpers, sentence-level streaming synthesis
│   │   └── text.py            # Prompt building, sentence chunking, SSE parsing, header encoding
│   ├── tests/                 # pytest suite
│   ├── voices/                # Piper ONNX voices (en_US-lessac-medium, hi_IN-rohan-medium)
│   ├── requirements.txt
│   └── requirements-dev.txt
├── frontend/
│   ├── index.html / app.js    # Voice chat UI (MediaRecorder, WebSocket, Web Audio PCM player)
│   ├── report.html / report.js# Model benchmark page
│   ├── style.css
│   └── favicon.svg
├── scripts/                   # Manual smoke tests and benchmark CLI
├── .github/workflows/ci.yml
├── .env.example
├── pyproject.toml             # Ruff + pytest configuration
└── package.json               # ESLint + Prettier for the frontend
```

## Known limitations

- Deepgram's live API cannot auto-detect the language, so *Streaming* modes treat "Auto-detect" as
  English.
- The embedding model is English-only, so retrieval quality for non-English questions is limited.
- Only English and Hindi have dedicated voices. Other languages are spoken with the English voice.
- Each WebSocket connection handles one question and answer, and there is no conversation memory
  between turns.

## Acknowledgements

Piper voices come from [rhasspy/piper-voices](https://huggingface.co/rhasspy/piper-voices). Check
each voice's model card for its license before you redistribute it.
