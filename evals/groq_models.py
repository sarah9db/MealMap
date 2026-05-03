from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

import requests


@dataclass(frozen=True)
class GroqModel:
    id: str
    raw: dict


def list_groq_models(api_key: str, timeout_s: float = 15) -> list[GroqModel]:
    """Return models visible to the provided Groq API key."""
    resp = requests.get(
        "https://api.groq.com/openai/v1/models",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        timeout=timeout_s,
    )
    resp.raise_for_status()
    data = resp.json()
    models: list[GroqModel] = []
    for item in data.get("data", []):
        mid = item.get("id")
        if mid:
            models.append(GroqModel(id=mid, raw=item))
    return sorted(models, key=lambda m: m.id)


def pick_default_text_candidates(model_ids: Iterable[str]) -> list[str]:
    """Heuristic: keep likely chat-capable text models for sweeps."""
    allow_prefixes = (
        "llama-",
        "meta-llama/",
        "openai/",
        "qwen/",
        "moonshotai/",
        "groq/",
    )
    deny_contains = ("whisper", "guard", "safeguard", "tts", "stt", "audio")

    out: list[str] = []
    for mid in model_ids:
        low = mid.lower()
        if not low.startswith(allow_prefixes):
            continue
        if any(x in low for x in deny_contains):
            continue
        out.append(mid)
    return out

