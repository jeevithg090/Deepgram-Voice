import time
import requests
import urllib.request
import os

# Download test audio file
AUDIO_URL = "https://dpgr.am/spacewalk.wav"
AUDIO_FILE = "spacewalk.wav"
API_URL = "http://localhost:8000/chat"

print("Downloading test audio...")
urllib.request.urlretrieve(AUDIO_URL, AUDIO_FILE)

def test_endpoint(language="en"):
    print(f"\n--- Testing Latency with Language: {language} ---")
    
    with open(AUDIO_FILE, "rb") as f:
        audio_data = f.read()

    files = {
        'audio': (AUDIO_FILE, audio_data, 'audio/wav')
    }
    data = {
        'language': language
    }
    
    print("Sending request to backend...")
    start_time = time.time()
    
    response = requests.post(API_URL, files=files, data=data)
    
    end_time = time.time()
    latency = end_time - start_time
    
    print(f"Total Latency: {latency:.2f} seconds")
    print(f"Response Status: {response.status_code}")
    
    if response.status_code == 200:
        # Try to parse hex transcript headers
        user_hex = response.headers.get("X-User-Transcript", "")
        agent_hex = response.headers.get("X-Agent-Transcript", "")
        
        user_text = bytes.fromhex(user_hex).decode('utf-8') if user_hex else "N/A"
        agent_text = bytes.fromhex(agent_hex).decode('utf-8') if agent_hex else "N/A"
        
        print(f"Transcription (STT): {user_text}")
        print(f"Agent Response (LLM mock): {agent_text}")
        print(f"Audio Output Size: {len(response.content)} bytes")
    else:
        print(f"Error: {response.text}")

if __name__ == "__main__":
    test_endpoint("en")
