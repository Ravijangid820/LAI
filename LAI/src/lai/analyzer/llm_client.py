"""Thin OpenAI-compatible vLLM client for the analyzer LLM.

Separate from serve_rag.py's llm_generate so the analyzer can:
  - point at a different endpoint (Qwen3.6-27B on :8005),
  - turn thinking mode on/off per call,
  - request guided JSON decoding.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass

import httpx


@dataclass
class AnalyzerLLMConfig:
    api_url: str
    model: str
    timeout_s: float = 600.0
    default_temperature: float = 0.2
    api_key: str | None = None


def from_env() -> AnalyzerLLMConfig | None:
    url = os.environ.get("ANALYZER_LLM_API_URL")
    if not url:
        return None
    return AnalyzerLLMConfig(
        api_url=url,
        model=os.environ.get("ANALYZER_LLM_MODEL", "qwen3.6-27b"),
        api_key=os.environ.get("ANALYZER_LLM_API_KEY"),
    )


_THINK_RE = re.compile(r"<think>.*?</think>\s*", re.DOTALL)


def _strip_thinking(text: str) -> str:
    return _THINK_RE.sub("", text).strip()


def call(
    cfg: AnalyzerLLMConfig,
    system: str,
    user: str,
    *,
    json_schema: dict | None = None,
    enable_thinking: bool = True,
    max_thinking_tokens: int = 8192,
    max_new_tokens: int = 4096,
    temperature: float | None = None,
) -> tuple[str, int]:
    """Single chat completion. Returns (content_without_thinking, thinking_tokens).

    The vLLM ``--enable-reasoning --reasoning-parser qwen3`` server-side
    setup separates reasoning from content automatically, but defensively
    we also strip ``<think>...</think>`` here.
    """
    body: dict = {
        "model": cfg.model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "max_tokens": max_new_tokens,
        "temperature": cfg.default_temperature if temperature is None else temperature,
    }
    # Qwen3 thinking-mode toggle via chat template kwargs
    if not cfg.api_key:
        body["chat_template_kwargs"] = {"enable_thinking": enable_thinking}
        if enable_thinking:
            body["max_completion_tokens"] = max_new_tokens + max_thinking_tokens

    if json_schema is not None:
        body["response_format"] = {
            "type": "json_schema",
            "json_schema": {
                "name": "schema",
                "schema": json_schema,
                "strict": True,
            },
        }

    headers = {"Content-Type": "application/json"}
    if cfg.api_key:
        headers["Authorization"] = f"Bearer {cfg.api_key}"

    url = cfg.api_url.rstrip("/") + "/v1/chat/completions"
    r = httpx.post(url, json=body, headers=headers, timeout=cfg.timeout_s)
    r.raise_for_status()
    data = r.json()
    msg = data["choices"][0]["message"]
    content = msg.get("content") or ""
    reasoning = msg.get("reasoning_content") or ""
    if reasoning:
        thinking_tokens = len(reasoning) // 4
    else:
        # Fallback: estimate from <think> stripped output
        thinking_tokens = (len(content) - len(_strip_thinking(content))) // 4
        content = _strip_thinking(content)
    return content.strip(), max(0, thinking_tokens)
