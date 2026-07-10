from piper import PiperVoice
import io
import struct
import queue
import threading
import asyncio

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

async def stream_synthesis(text: str, language: str, client_ws):
    try:
        voice = get_piper_voice(language)
        header = create_wav_header(voice.config.sample_rate)
        await client_ws.send_bytes(header)
        
        audio_queue = queue.Queue()
        
        def synthesize_worker():
            try:
                for chunk in voice.synthesize(text):
                    audio_queue.put(chunk.audio_int16_bytes)
            except Exception as e:
                print(f"Piper error: {e}")
            finally:
                audio_queue.put(None) 
                
        threading.Thread(target=synthesize_worker, daemon=True).start()
        
        while True:
            chunk_bytes = await asyncio.to_thread(audio_queue.get)
            if chunk_bytes is None:
                break
            await client_ws.send_bytes(chunk_bytes)
            
    except Exception as e:
        print(f"Exception during synthesis stream: {e}")
