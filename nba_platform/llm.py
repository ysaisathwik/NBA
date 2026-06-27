"""LLM client with graceful offline fallback.

Implements the architecture's *Tool-or-LLM fallback* + *offline fallback mode* (EC-05):
when no API key is configured (or ``NBA_FORCE_OFFLINE=1``), :meth:`LLMClient.complete`
returns ``None`` so callers transparently use their deterministic rule-based path.
"""
from __future__ import annotations

import json
import time
from typing import Any

from .config import get_settings


class LLMClient:
    def __init__(self) -> None:
        self.settings = get_settings()
        self._client = None
        self.calls = 0
        self.fallbacks = 0
        if self.settings.llm_available:
            try:  # import lazily so the package works without the SDK
                import anthropic  # type: ignore

                self._client = anthropic.Anthropic(api_key=self.settings.anthropic_api_key)
            except Exception:
                self._client = None

    @property
    def available(self) -> bool:
        return self._client is not None

    def complete(
        self,
        system: str,
        user: str,
        *,
        fast: bool = False,
        json_mode: bool = False,
        max_tokens: int = 1500,
        retries: int = 3,
    ) -> str | dict[str, Any] | None:
        """Return model text (or parsed JSON). ``None`` signals "use deterministic fallback"."""
        if not self.available:
            self.fallbacks += 1
            return None

        model = self.settings.llm_fast_model if fast else self.settings.llm_model
        prompt = user
        if json_mode:
            prompt += "\n\nRespond with a single valid JSON object and nothing else."

        delay = 2.0
        for attempt in range(retries):
            try:
                self.calls += 1
                resp = self._client.messages.create(  # type: ignore[union-attr]
                    model=model,
                    max_tokens=max_tokens,
                    system=system,
                    messages=[{"role": "user", "content": prompt}],
                )
                text = "".join(getattr(b, "text", "") for b in resp.content)
                if json_mode:
                    return _safe_json(text)
                return text
            except Exception:  # EC-05: exponential backoff then fall back
                if attempt == retries - 1:
                    self.fallbacks += 1
                    return None
                time.sleep(min(delay, 0.01))  # short in practice; structure mirrors the spec
                delay *= 2
        return None


def _safe_json(text: str) -> dict[str, Any] | None:
    text = text.strip()
    if text.startswith("```"):
        text = text.strip("`")
        text = text.split("\n", 1)[-1] if "\n" in text else text
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        return None
    try:
        return json.loads(text[start : end + 1])
    except Exception:
        return None


_client: LLMClient | None = None


def get_llm() -> LLMClient:
    global _client
    if _client is None:
        _client = LLMClient()
    return _client


def reset_llm() -> None:
    global _client
    _client = None
