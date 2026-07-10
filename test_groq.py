import requests
import os
api_key = os.environ.get("GROQ_API_KEY")
url = "https://api.groq.com/openai/v1/chat/completions"

headers = {
    "Authorization": f"Bearer {api_key}",
    "Content-Type": "application/json"
}

data = {
    "model": "llama-3.1-8b-instant",
    "messages": [
        {"role": "system", "content": "You are a fast, helpful AI assistant."},
        {"role": "user", "content": "Hello"}
    ],
    "temperature": 0.5,
    "max_tokens": 150
}

response = requests.post(url, headers=headers, json=data)
print(response.status_code)
print(response.text)
