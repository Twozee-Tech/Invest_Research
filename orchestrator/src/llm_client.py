"""OpenAI-compatible LLM client for llama-swap with model switching and retry."""

from __future__ import annotations

import json
import os
import re

import httpx
import structlog
from openai import OpenAI

logger = structlog.get_logger()

DEFAULT_TIMEOUT = 300  # LLM can be slow, especially thinking models
MAX_RETRIES = 2


class LLMClient:
    """Client for llama-swap via OpenAI-compatible API.

    llama-swap auto-swaps models based on the `model` field in requests.
    """

    def __init__(self, base_url: str | None = None):
        self.base_url = (base_url or os.environ.get("LLM_BASE_URL", "http://192.168.0.169:8080/v1")).rstrip("/")
        # Ensure base_url ends with /v1 for OpenAI client
        oai_base = self.base_url if self.base_url.endswith("/v1") else f"{self.base_url}/v1"
        self._client = OpenAI(
            base_url=oai_base,
            api_key="not-needed",
            timeout=DEFAULT_TIMEOUT,
        )
        self._http = httpx.Client(timeout=30.0)

    def list_models(self) -> list[str]:
        """Get available models from llama-swap."""
        try:
            models = self._client.models.list()
            return [m.id for m in models.data]
        except Exception as e:
            logger.error("llm_list_models_failed", error=str(e))
            return []

    def chat(
        self,
        messages: list[dict[str, str]],
        model: str = "Qwen3-Next",
        temperature: float = 0.7,
        max_tokens: int = 4096,
    ) -> str:
        """Send chat completion request. Returns the assistant message content."""
        last_error = None
        for attempt in range(MAX_RETRIES + 1):
            try:
                response = self._client.chat.completions.create(
                    model=model,
                    messages=messages,
                    temperature=temperature,
                    max_tokens=max_tokens,
                )
                msg = response.choices[0].message
                content = msg.content or ""
                # Some thinking models put output in reasoning_content
                if not content and hasattr(msg, "reasoning_content") and msg.reasoning_content:
                    content = msg.reasoning_content
                logger.info(
                    "llm_chat_completed",
                    model=model,
                    prompt_tokens=getattr(response.usage, "prompt_tokens", None),
                    completion_tokens=getattr(response.usage, "completion_tokens", None),
                )
                return content
            except Exception as e:
                last_error = e
                logger.warning(
                    "llm_chat_failed",
                    model=model,
                    attempt=attempt + 1,
                    error=str(e),
                )
        raise RuntimeError(f"LLM chat failed after {MAX_RETRIES + 1} attempts: {last_error}")

    def chat_json(
        self,
        messages: list[dict[str, str]],
        model: str = "Qwen3-Next",
        fallback_model: str | None = "Nemotron",
        temperature: float = 0.7,
        max_tokens: int = 4096,
    ) -> dict:
        """Send chat request and parse JSON from response.

        Tries primary model first; if JSON parsing fails and fallback_model
        is set, retries with the fallback.
        """
        for current_model in [model, fallback_model] if fallback_model else [model]:
            try:
                raw = self.chat(
                    messages=messages,
                    model=current_model,
                    temperature=temperature,
                    max_tokens=max_tokens,
                )
                return self._extract_json(raw)
            except (json.JSONDecodeError, ValueError) as e:
                logger.warning(
                    "llm_json_parse_failed",
                    model=current_model,
                    error=str(e),
                )
                if current_model == (fallback_model or model):
                    raise
        raise ValueError("Failed to get valid JSON from LLM")

    @staticmethod
    def _extract_json(text: str) -> dict:
        """Extract JSON from LLM response, handling markdown code blocks."""
        # Try to find JSON in code blocks first
        match = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", text, re.DOTALL)
        if match:
            return json.loads(match.group(1).strip())
        # Try to find raw JSON object
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if match:
            return json.loads(match.group(0))
        raise ValueError(f"No JSON found in response: {text[:200]}...")

    def close(self) -> None:
        self._http.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
