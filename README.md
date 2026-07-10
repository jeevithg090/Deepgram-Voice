# Voice AI Agent

A full-stack, real-time voice conversational AI agent featuring Speech-to-Text (STT), a Retrieval-Augmented Generation (RAG) knowledge base, Large Language Model (LLM) reasoning, and Text-to-Speech (TTS).

## Features

- **Real-time Streaming**: Uses WebSockets to stream audio directly from the browser for ultra-low latency.
- **Speech-to-Text (STT)**: Integrated with the Deepgram API for lightning-fast and accurate speech transcription.
- **RAG Knowledge Base**: Uses `fastembed` for local, fast semantic embeddings to answer questions based on a mock e-commerce FAQ dataset.
- **LLM Reasoning**: Supports both Google Gemini (via `google-genai`) and Groq APIs to generate context-aware responses.
- **Local Text-to-Speech (TTS)**: Uses ONNX models (e.g., Piper TTS) for local, fast text-to-speech generation in both English and Hindi.
- **FastAPI Backend**: A robust asynchronous backend serving both the WebSocket/REST API and static frontend files.
- **Vanilla Frontend**: Lightweight, fast frontend built with HTML, CSS, and Vanilla JavaScript, requiring no build steps.

## Project Structure

```
├── backend/
│   ├── main.py                 # FastAPI application and routing
│   ├── deepgram_service.py     # Deepgram STT integration
│   ├── llm_service.py          # Gemini/Groq LLM integration
│   ├── knowledge_base.py       # FastEmbed RAG implementation
│   └── *.onnx                  # Local TTS models (English & Hindi)
├── frontend/
│   ├── index.html              # Main UI
│   ├── style.css               # Styling
│   └── app.js                  # Frontend logic & WebSocket management
└── test_*.py                   # Various test scripts for latency and services
```

## Setup & Installation

1. **Clone the repository** (or extract the zip).
2. **Navigate to the backend directory**:
   ```bash
   cd backend
   ```
3. **Set up a virtual environment and install dependencies** (assuming `fastapi`, `uvicorn`, `deepgram-sdk`, `google-genai`, `fastembed`, etc. are required):
   ```bash
   python3 -m venv venv
   source venv/bin/activate
   pip install -r requirements.txt  # (if available)
   ```
4. **Set your API Keys**:
   Ensure you have your environment variables set for:
   - `DEEPGRAM_API_KEY`
   - `GEMINI_API_KEY` or `GROQ_API_KEY`
   
   ```bash
   export DEEPGRAM_API_KEY="your-deepgram-key"
   export GROQ_API_KEY="your-groq-key"
   ```

## Running the Application

1. From within the `backend` directory, start the FastAPI server:
   ```bash
   uvicorn main:app --host 0.0.0.0 --port 8000
   ```
   *(Note: If port 8000 is in use, try `--port 8001` or another available port.)*

2. Open your web browser and navigate to the printed address (e.g., `http://localhost:8000`).
3. Allow microphone permissions when prompted, choose your mode (Batch or Streaming LLM), select a language, and start talking!

## Usage Modes

- **Batch Mode**: Records a complete audio snippet before sending it to the backend for processing.
- **Streaming LLM**: Streams audio chunks to the backend in real-time, significantly reducing the Time To First Byte (TTFB) for a snappy conversational experience.
