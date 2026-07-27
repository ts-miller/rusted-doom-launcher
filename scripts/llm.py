"""Shared LLM client for pipeline scripts.

All text-generation scripts call OpenRouter with the model configured in
scripts/config.json. Requires OPENROUTER_API_KEY in the environment.
Fails loudly on any error — no fallback models, no silent retries.

Not a standalone script: import from a sibling `uv run` script whose
inline dependencies include `requests`.
"""

import json
import os
from pathlib import Path

import requests

CONFIG_FILE = Path(__file__).parent / "config.json"
API_URL = "https://openrouter.ai/api/v1/chat/completions"


def load_llm_config() -> dict:
    return json.loads(CONFIG_FILE.read_text())["llm"]


def chat(prompt: str, *, temperature: float | None = None, web_search: bool = False) -> str:
    """Send a single-turn prompt, return the model's text response."""
    cfg = load_llm_config()
    if cfg["provider"] != "openrouter":
        raise ValueError(f"Unsupported provider in config.json: {cfg['provider']}")

    api_key = os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        raise RuntimeError("OPENROUTER_API_KEY environment variable not set")

    body = {
        "model": cfg["model"],
        "temperature": cfg["temperature"] if temperature is None else temperature,
        "messages": [{"role": "user", "content": prompt}],
    }
    if web_search:
        body["plugins"] = [{"id": "web"}]

    response = requests.post(
        API_URL,
        headers={"Authorization": f"Bearer {api_key}"},
        json=body,
        timeout=180,
    )
    response.raise_for_status()
    data = response.json()
    if "error" in data:
        raise RuntimeError(f"OpenRouter error: {data['error']}")

    content = data["choices"][0]["message"]["content"]
    if not content or not content.strip():
        raise RuntimeError(f"Empty response from {cfg['model']}")
    return content.strip()


def chat_json(prompt: str, *, temperature: float | None = None, web_search: bool = False) -> dict:
    """chat(), but parse the response as JSON (tolerating markdown fences)."""
    text = chat(prompt, temperature=temperature, web_search=web_search)
    if text.startswith("```"):
        text = text.split("\n", 1)[1]
        text = text.rsplit("```", 1)[0]
    return json.loads(text.strip())
