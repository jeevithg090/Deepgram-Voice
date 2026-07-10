import asyncio
import websockets
import json
import time

async def run_test():
    with open("spacewalk.wav", "rb") as f:
        audio_data = f.read()

    url = "ws://localhost:8000/ws/chat"
    
    async with websockets.connect(url) as ws:
        print("--- Testing Streaming Latency ---")
        
        # 1. Send configuration
        config = {
            "language": "en",
            "use_llm": True
        }
        await ws.send(json.dumps(config))
        print("Sent config.")
        
        # 2. Start sender and receiver concurrently
        
        async def sender():
            chunk_size = 4096
            for i in range(0, len(audio_data), chunk_size):
                await ws.send(audio_data[i:i+chunk_size])
                await asyncio.sleep(0.01)
            
            print("Finished sending audio. Waiting for AI response...")
            await ws.send(b"") # Close the stream
            
        async def receiver():
            stt_end_time = None
            first_byte_time = None
            total_audio_received = 0
            
            try:
                while True:
                    msg = await ws.recv()
                    if isinstance(msg, str):
                        data = json.loads(msg)
                        if data.get("type") == "transcript":
                            if data['speaker'] == 'user':
                                stt_end_time = time.time()
                            print(f"[{data['speaker']}] {data['text']}")
                        elif data.get("type") == "audio_start":
                            first_byte_time = time.time()
                            if stt_end_time:
                                ttfb = first_byte_time - stt_end_time
                                print(f"--> LLM+TTS Latency (STT End to First Byte): {ttfb*1000:.2f} ms <--")
                            else:
                                print(f"--> TTFB (Time to First Byte, total): {(first_byte_time - start_time)*1000:.2f} ms <--")
                        elif data.get("type") == "audio_end":
                            print(f"Audio stream finished. Total TTS bytes: {total_audio_received}")
                            break
                    else:
                        total_audio_received += len(msg)
            except websockets.exceptions.ConnectionClosed:
                print("Connection closed.")
                
        start_time = time.time()
        await asyncio.gather(sender(), receiver())

if __name__ == "__main__":
    asyncio.run(run_test())
