import os
import json
from fastapi import FastAPI, UploadFile, File, Form, Response, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from deepgram_service import transcribe_audio, synthesize_audio, stream_transcription, stream_synthesis
from knowledge_base import kb
from llm_service import generate_response, generate_response_openai, generate_response_openai_stream
from pydantic import BaseModel
from typing import List
import time

app = FastAPI(title="Voice AI Agent")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- BATCH ENDPOINT ---
@app.post("/chat")
async def chat(audio: UploadFile = File(...), language: str = Form(...), use_llm: bool = Form(False)):
    audio_bytes = await audio.read()
    user_transcript = transcribe_audio(audio_bytes, language)
    
    if not user_transcript.strip():
        return JSONResponse({"error": "Could not understand the audio"}, status_code=400)
    
    context = kb.search(user_transcript, top_k=2)
    if use_llm:
        agent_text = generate_response_openai(user_transcript, context, language)
    else:
        agent_text = generate_response(user_transcript, context, language)
        
    agent_audio_bytes = synthesize_audio(agent_text, language)
    
    headers = {
        "X-User-Transcript": str(user_transcript.encode('utf-8').hex()),
        "X-Agent-Transcript": str(agent_text.encode('utf-8').hex())
    }
    return Response(content=agent_audio_bytes, media_type="audio/wav", headers=headers)

# --- STREAMING WEBSOCKET ENDPOINT ---
@app.websocket("/ws/chat")
async def websocket_chat(websocket: WebSocket):
    await websocket.accept()
    
    # First message from client should be JSON with language
    try:
        config_msg = await websocket.receive_text()
        config = json.loads(config_msg)
        language = config.get("language", "en")
        encoding = config.get("encoding", None)
        sample_rate = config.get("sample_rate", None)
        use_llm = config.get("use_llm", False)
    except Exception:
        language = "en"
        encoding = None
        sample_rate = None
        use_llm = False
        
    async def sentence_generator(transcript: str, retrieved_context: str, language: str):
        from llm_service import generate_response_openai_stream
        import re
        buffer = ""
        full_text = ""
        async for token in generate_response_openai_stream(transcript, retrieved_context, language):
            buffer += token
            full_text += token
            
            # Update frontend continuously if desired, but we'll do it per sentence for now
            match = re.search(r'([.!?।\n]+)(\s*)', buffer)
            if match:
                end_pos = match.end()
                sentence = buffer[:end_pos]
                buffer = buffer[end_pos:]
                if sentence.strip():
                    yield sentence.strip()
                    await websocket.send_text(json.dumps({"type": "transcript", "speaker": "agent", "text": full_text}))
        
        if buffer.strip():
            yield buffer.strip()
            full_text += buffer
            await websocket.send_text(json.dumps({"type": "transcript", "speaker": "agent", "text": full_text}))
            
    async def process_final_transcript(transcript: str):
        import time
        t0 = time.time()
        # Send transcript to frontend immediately
        await websocket.send_text(json.dumps({"type": "transcript", "speaker": "user", "text": transcript}))
        t1 = time.time()
        print(f"Sent user transcript in {t1-t0:.3f}s")
        
        # Generate response using LLM & KB
        retrieved_context = kb.search(transcript)
        t2 = time.time()
        print(f"KB search in {t2-t1:.3f}s")
        
        if use_llm:
            # Start streaming TTS back immediately using our generator
            await websocket.send_text(json.dumps({"type": "audio_start"}))
            t3 = time.time()
            print(f"Sent audio_start in {t3-t2:.3f}s")
            await stream_synthesis(sentence_generator(transcript, retrieved_context, language), language, websocket)
            await websocket.send_text(json.dumps({"type": "audio_end"}))
            print(f"Finished stream_synthesis in {time.time()-t3:.3f}s")
        else:
            agent_text = generate_response(transcript, retrieved_context, language)
            print(f"Agent Response: {agent_text}")
            await websocket.send_text(json.dumps({"type": "transcript", "speaker": "agent", "text": agent_text}))
            
            # Start streaming TTS back
            await websocket.send_text(json.dumps({"type": "audio_start"}))
            # Fallback for non-LLM: pass an async generator that yields the single string
            async def single_sentence():
                yield agent_text
            await stream_synthesis(single_sentence(), language, websocket)
            await websocket.send_text(json.dumps({"type": "audio_end"}))
        
        # Close connection after a single interaction
        await websocket.close()

    try:
        await stream_transcription(websocket, language, encoding, sample_rate, process_final_transcript)
    except WebSocketDisconnect:
        print("Client disconnected")

class BenchmarkRequest(BaseModel):
    api_key: str
    models: List[str]
    prompt: str

@app.post("/benchmark_stream")
async def benchmark_stream(req: BenchmarkRequest):
    results = []
    retrieved_context = "This is a test context for benchmarking."
    language = "en"
    
    for model in req.models:
        start_time = time.time()
        ttft = None
        token_count = 0
        
        try:
            async for token in generate_response_openai_stream(req.prompt, retrieved_context, language, model=model, api_key=req.api_key):
                if ttft is None and token.strip():
                    ttft = time.time() - start_time
                token_count += 1
            
            end_time = time.time()
            total_time = end_time - start_time
            tps = token_count / total_time if total_time > 0 else 0
            
            results.append({
                "model": model,
                "ttft": ttft if ttft is not None else 0,
                "tps": tps,
                "total_time": total_time,
                "error": None
            })
        except Exception as e:
            results.append({
                "model": model,
                "ttft": 0,
                "tps": 0,
                "total_time": 0,
                "error": str(e)
            })
            
    results.sort(key=lambda x: x["tps"], reverse=True)
    return JSONResponse(results)

# Mount the frontend directory
app.mount("/", StaticFiles(directory="../frontend", html=True), name="frontend")
