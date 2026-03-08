"""LLM client for Qwen via vLLM OpenAI-compatible API.

The LLM is used strictly as a text formatter — all legal information
must come from retrieved context.
"""

import time
from dataclasses import dataclass

import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from lai.core.config import get_settings
from lai.core.exceptions import LLMError
from lai.core.logging import get_logger

logger = get_logger("lai.generation.llm_client")


@dataclass
class LLMResponse:
    text: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    latency_ms: float = 0.0
    finish_reason: str | None = None
    error: str | None = None

    @property
    def success(self) -> bool:
        return self.error is None and bool(self.text)


class LLMClient:
    """Client for LLM generation via vLLM."""

    def __init__(self) -> None:
        settings = get_settings().llm
        self._url = settings.url
        self._model = settings.model
        self._temperature = settings.temperature
        self._max_tokens = settings.max_tokens
        self._top_p = settings.top_p
        self._timeout = settings.timeout
        self._client: httpx.Client | None = None
        self._total_requests = 0
        self._total_tokens = 0
        self._total_errors = 0
        logger.info("LLM client initialized: model=%s, url=%s, temp=%.1f", self._model, self._url, self._temperature)

    def _get_client(self) -> httpx.Client:
        if self._client is None or self._client.is_closed:
            self._client = httpx.Client(
                timeout=httpx.Timeout(self._timeout),
                limits=httpx.Limits(max_connections=20, max_keepalive_connections=5),
            )
        return self._client

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        retry=retry_if_exception_type((httpx.TimeoutException, httpx.ConnectError)),
    )
    def generate(
        self,
        system_prompt: str,
        user_message: str,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> LLMResponse:
        """Generate a response from the LLM."""
        start = time.perf_counter()
        self._total_requests += 1

        request_body = {
            "model": self._model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message},
            ],
            "temperature": temperature or self._temperature,
            "max_tokens": max_tokens or self._max_tokens,
            "top_p": self._top_p,
            "stream": False,
        }

        try:
            client = self._get_client()
            response = client.post(f"{self._url}/chat/completions", json=request_body)
            latency = (time.perf_counter() - start) * 1000

            if response.status_code != 200:
                self._total_errors += 1
                error = response.text[:500]
                logger.error("LLM returned %d: %s", response.status_code, error)
                return LLMResponse(text="", latency_ms=latency, error=f"HTTP {response.status_code}: {error}")

            result = response.json()
            choices = result.get("choices", [])
            if not choices:
                self._total_errors += 1
                return LLMResponse(text="", latency_ms=latency, error="No choices in response")

            text = choices[0].get("message", {}).get("content", "")
            usage = result.get("usage", {})
            total = usage.get("total_tokens", 0)
            self._total_tokens += total

            finish = choices[0].get("finish_reason")
            if finish == "length":
                logger.warning("LLM response truncated (max_tokens=%d)", max_tokens or self._max_tokens)

            logger.info("LLM generated %d tokens in %.1fms", usage.get("completion_tokens", 0), latency)
            return LLMResponse(
                text=text,
                prompt_tokens=usage.get("prompt_tokens", 0),
                completion_tokens=usage.get("completion_tokens", 0),
                total_tokens=total,
                latency_ms=latency,
                finish_reason=finish,
            )

        except (httpx.TimeoutException, httpx.ConnectError):
            self._total_errors += 1
            raise
        except Exception as e:
            self._total_errors += 1
            latency = (time.perf_counter() - start) * 1000
            logger.error("LLM request failed: %s", e)
            return LLMResponse(text="", latency_ms=latency, error=str(e))

    def generate_safe(self, system_prompt: str, user_message: str, **kwargs) -> LLMResponse:
        """Generate with automatic fallback — never raises."""
        try:
            return self.generate(system_prompt, user_message, **kwargs)
        except Exception as e:
            logger.error("LLM generation failed completely: %s", e)
            return LLMResponse(text="", error=f"All retries failed: {e}")

    def check_health(self) -> dict:
        try:
            client = self._get_client()
            start = time.perf_counter()
            response = client.get(f"{self._url.rstrip('/chat/completions')}/health", timeout=5.0)
            latency = (time.perf_counter() - start) * 1000
            return {"status": "healthy" if response.status_code == 200 else "unhealthy", "model": self._model, "latency_ms": latency}
        except Exception as e:
            return {"status": "unhealthy", "model": self._model, "error": str(e)}

    def get_metrics(self) -> dict:
        return {"total_requests": self._total_requests, "total_tokens": self._total_tokens, "total_errors": self._total_errors}

    def close(self) -> None:
        if self._client and not self._client.is_closed:
            self._client.close()


_llm_client: LLMClient | None = None


def get_llm_client() -> LLMClient:
    global _llm_client
    if _llm_client is None:
        _llm_client = LLMClient()
    return _llm_client
