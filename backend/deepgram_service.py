import os
import json
import asyncio
import requests
import websockets
from websockets.exceptions import ConnectionClosed
from piper import PiperVoice
import io
import struct
import queue
import threading

API_KEY = os.environ.get("DEEPGRAM_API_KEY")
HEADERS = {
    "Authorization": f"Token {API_KEY}"
}

piper_en = None
piper_hi = None

def get_piper_voice(language: str):
    global piper_en, piper_hi
    if language == "hi":
        if piper_hi is None:
            piper_hi = PiperVoice.load("hi_IN-rohan-medium.onnx")
        return piper_hi
    else:
        if piper_en is None:
            piper_en = PiperVoice.load("en_US-lessac-medium.onnx")
        return piper_en

def create_wav_header(sample_rate: int = 22050) -> bytes:
    header = b'RIFF' + struct.pack('<I', 0xFFFFFFFF) + b'WAVE'
    header += b'fmt ' + struct.pack('<I', 16) 
    header += struct.pack('<H', 1) 
    header += struct.pack('<H', 1) 
    header += struct.pack('<I', sample_rate) 
    header += struct.pack('<I', sample_rate * 2) 
    header += struct.pack('<H', 2) 
    header += struct.pack('<H', 16) 
    header += b'data' + struct.pack('<I', 0xFFFFFFFF) 
    return header

# --- BATCH REST FUNCTIONS ---
def transcribe_audio(audio_bytes: bytes, language: str) -> str:
    try:
        url = "https://api.deepgram.com/v1/listen?model=nova-2"
        if language == "auto":
            url += "&detect_language=true"
        else:
            url += f"&language={language}"
            
        response = requests.post(url, headers={**HEADERS, "Content-Type": "audio/webm"}, data=audio_bytes)
        response.raise_for_status()
        data = response.json()
        transcript = data["results"]["channels"][0]["alternatives"][0]["transcript"]
        return transcript
    except Exception as e:
        print(f"Exception during transcription: {e}")
        return ""

def synthesize_audio(text: str, language: str = "en") -> bytes:
    try:
        voice = get_piper_voice(language)
        # Use full bytes in memory
        chunks = []
        for chunk in voice.synthesize(text):
            chunks.append(chunk.audio_int16_bytes)
        
        # We need a proper WAV header with correct sizes for batch
        import wave
        buffer = io.BytesIO()
        with wave.open(buffer, "wb") as wav_file:
            wav_file.setnchannels(1)
            wav_file.setsampwidth(2)
            wav_file.setframerate(voice.config.sample_rate)
            wav_file.writeframes(b"".join(chunks))
            
        return buffer.getvalue()
    except Exception as e:
        print(f"Exception during synthesis: {e}")
        return b""

# --- STREAMING WEBSOCKET FUNCTIONS ---
async def stream_transcription(client_ws, language: str, encoding: str, sample_rate: int, on_final_transcript_callback):
    """
    Connects to Deepgram WS, pipes audio from client_ws to Deepgram,
    and calls the callback with final transcriptions.
    """
    url = f"wss://api.deepgram.com/v1/listen?model=nova-2"
    if encoding:
        url += f"&encoding={encoding}"
    if sample_rate:
        url += f"&sample_rate={sample_rate}"
        
    # Deepgram streaming API does not support detect_language=true
    if language == "auto":
        language = "en" # Fallback to English since auto isn't supported
    url += f"&language={language}"

    try:
        print(f"Connecting to Deepgram with URL: {url}")
        async with websockets.connect(url, additional_headers={"Authorization": f"Token {API_KEY}"}) as dg_ws:
            
            async def sender():
                try:
                    while True:
                        try:
                            data = await client_ws.receive_bytes()
                        except Exception: # Includes WebSocketDisconnect
                            data = b""
                        if not data:
                            break
                        await dg_ws.send(data)
                except Exception as e:
                    print(f"Sender closed: {e}")
                finally:
                    # Send an empty JSON to tell Deepgram we are done (CloseStream)
                    try:
                        await dg_ws.send(json.dumps({"type": "CloseStream"}))
                    except:
                        pass
            
            async def receiver():
                final_transcript = ""
                try:
                    while True:
                        msg = await dg_ws.recv()
                        res = json.loads(msg)
                        if res.get("is_final"):
                            words = res.get("channel", {}).get("alternatives", [{}])[0].get("transcript", "")
                            if words:
                                final_transcript += words + " "
                        
                        # Check for end of stream
                        if res.get("type") == "CloseStream":
                            break
                except Exception as e:
                    print(f"Receiver closed: {e}")
                
                if final_transcript.strip():
                    await on_final_transcript_callback(final_transcript.strip())

            await asyncio.gather(sender(), receiver())
            
    except Exception as e:
        print(f"Deepgram WS connection error: {e}")

async def stream_synthesis(sentence_iterator, language: str, client_ws):
    """
    Synthesizes audio using Piper TTS and streams raw WAV frames back to the websocket.
    Accepts an async iterator of sentences.
    """
    try:
        voice = get_piper_voice(language)
        header = create_wav_header(voice.config.sample_rate)
        await client_ws.send_bytes(header)
        
        audio_queue = queue.Queue()
        sentence_queue = queue.Queue()
        
        def synthesize_worker():
            try:
                while True:
                    sentence = sentence_queue.get()
                    if sentence is None:
                        break
                    for chunk in voice.synthesize(sentence):
                        audio_queue.put(chunk.audio_int16_bytes)
            except Exception as e:
                print(f"Piper error: {e}")
            finally:
                audio_queue.put(None) 
                
        threading.Thread(target=synthesize_worker, daemon=True).start()
        
        async def feed_sentences():
            try:
                async for sentence in sentence_iterator:
                    sentence_queue.put(sentence)
            except Exception as e:
                print(f"Error feeding sentences: {e}")
            finally:
                sentence_queue.put(None)
                
        asyncio.create_task(feed_sentences())
        
        while True:
            chunk_bytes = await asyncio.to_thread(audio_queue.get)
            if chunk_bytes is None:
                break
            await client_ws.send_bytes(chunk_bytes)
            
    except Exception as e:
        print(f"Exception during synthesis stream: {e}")
