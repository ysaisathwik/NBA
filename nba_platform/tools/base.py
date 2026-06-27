"""Tool primitives: a typed callable + a registry."""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Callable


@dataclass
class ToolCall:
    tool: str
    input: dict[str, Any]
    output: Any
    duration_ms: int
    error: str | None = None


class Tool:
    """A named, deterministic capability. Subclass or pass ``fn`` for quick tools."""

    def __init__(self, name: str, ttype: str, description: str, fn: Callable[..., Any] | None = None) -> None:
        self.name = name
        self.type = ttype  # redis | supabase | rest | internal | llm | vector
        self.description = description
        self._fn = fn

    def _call(self, **kwargs: Any) -> Any:  # pragma: no cover - overridden or fn provided
        if self._fn is None:
            raise NotImplementedError(self.name)
        return self._fn(**kwargs)

    def run(self, **kwargs: Any) -> ToolCall:
        start = time.perf_counter()
        error = None
        output: Any = None
        try:
            output = self._call(**kwargs)
        except Exception as exc:  # tools must never crash an agent; surface as error
            error = f"{type(exc).__name__}: {exc}"
        dur = int((time.perf_counter() - start) * 1000)
        return ToolCall(tool=self.name, input=kwargs, output=output, duration_ms=dur, error=error)


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        self._tools[tool.name] = tool

    def get(self, name: str) -> Tool:
        if name not in self._tools:
            raise KeyError(f"tool not registered: {name}")
        return self._tools[name]

    def has(self, name: str) -> bool:
        return name in self._tools

    def all(self) -> list[Tool]:
        return list(self._tools.values())
