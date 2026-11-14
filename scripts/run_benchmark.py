"""Run the OpenAI model benchmark against a running server and print a ranked table.

Uses ``OPENAI_API_KEY`` from the environment.

    python scripts/run_benchmark.py [--url http://localhost:8000] [--models gpt-4o-mini gpt-4o]
"""

from __future__ import annotations

import argparse
import os
import sys

import httpx

DEFAULT_MODELS = ["gpt-4o-mini", "gpt-3.5-turbo", "gpt-4o"]
DEFAULT_PROMPT = "Explain the history of artificial intelligence in 3 paragraphs."


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--url", default="http://localhost:8000")
    parser.add_argument("--models", nargs="+", default=DEFAULT_MODELS)
    parser.add_argument("--prompt", default=DEFAULT_PROMPT)
    args = parser.parse_args()

    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        print("Set OPENAI_API_KEY first.", file=sys.stderr)
        return 1

    response = httpx.post(
        f"{args.url}/benchmark_stream",
        json={"api_key": api_key, "models": args.models, "prompt": args.prompt},
        timeout=300,
    )
    if response.status_code != 200:
        print(f"HTTP {response.status_code}: {response.text}", file=sys.stderr)
        return 1

    print(f"{'#':>3}  {'model':<24} {'TTFT (s)':>9} {'chunks/s':>9} {'total (s)':>10}")
    for rank, row in enumerate(response.json(), start=1):
        if row["error"]:
            print(f"{'-':>3}  {row['model']:<24} error: {row['error']}")
        else:
            print(
                f"{rank:>3}  {row['model']:<24} {row['ttft']:>9.3f} "
                f"{row['tps']:>9.2f} {row['total_time']:>10.2f}"
            )
    return 0


if __name__ == "__main__":
    sys.exit(main())
