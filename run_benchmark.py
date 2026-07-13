import requests
import json
import time

import os

api_key = os.environ.get("OPENAI_API_KEY", "your-api-key-here")
models = ["gpt-4o-mini", "gpt-3.5-turbo", "gpt-3.5-turbo-0125", "gpt-3.5-turbo-1106", "gpt-4o"]
prompt = "Explain the history of artificial intelligence in 3 paragraphs."

url = "http://127.0.0.1:8000/benchmark_stream"

payload = {
    "api_key": api_key,
    "models": models,
    "prompt": prompt
}

print("Running benchmark...")
response = requests.post(url, json=payload)
results = response.json()

print(json.dumps(results, indent=2))

html_rows = ""
for index, result in enumerate(results):
    rank_class = ""
    if index == 0:
        rank_class = "rank-1"
    elif index == 1:
        rank_class = "rank-2"
    elif index == 2:
        rank_class = "rank-3"
        
    if result.get("error"):
        html_rows += f'''
        <tr>
            <td>-</td>
            <td>{result["model"]}</td>
            <td colspan="3" style="color: #ff3333;">Error: {result["error"]}</td>
        </tr>'''
    else:
        html_rows += f'''
        <tr>
            <td class="{rank_class}">#{index + 1}</td>
            <td>{result["model"]}</td>
            <td>{result["ttft"]:.3f}</td>
            <td>{result["tps"]:.2f}</td>
            <td>{result["total_time"]:.2f}</td>
        </tr>'''

print("\nHTML output to inject:")
print(html_rows)
