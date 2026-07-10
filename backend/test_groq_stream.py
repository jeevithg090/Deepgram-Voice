import asyncio
import httpx
import json
import os
async def test_stream():
    api_key = os.environ.get("GROQ_API_KEY")
    url = "https://api.groq.com/openai/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }
    data = {
        "model": "llama-3.1-8b-instant",
        "messages": [
            {"role": "user", "content": "Write a 3 sentence story."}
        ],
        "temperature": 0.5,
        "max_tokens": 150,
        "stream": True
    }
    
    async with httpx.AsyncClient() as client:
        async with client.stream("POST", url, headers=headers, json=data) as response:
            async for line in response.aiter_lines():
                if line.startswith("data: "):
                    line = line[6:]
                    if line == "[DONE]":
                        break
                    try:
                        chunk = json.loads(line)
                        content = chunk["choices"][0]["delta"].get("content", "")
                        if content:
                            print(content, end="", flush=True)
                    except json.JSONDecodeError:
                        pass
    print("\nDone.")

asyncio.run(test_stream())
