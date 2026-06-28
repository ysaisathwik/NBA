"""Agent base class with timing, tool-call recording, and result persistence."""
from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # avoid import cycles at runtime
    from ..runtime import Platform, Session


@dataclass
class AgentResult:
    name: str
    output: dict[str, Any]
    confidence: float = 0.0
    status: str = "completed"  # completed | failed | timeout | skipped
    latency_ms: int = 0
    mode: str = "tool"  # tool | llm | fallback
    tool_calls: list[dict[str, Any]] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


class Agent:
    """Base for every specialist agent.

    Subclasses implement :meth:`_run`, set ``self._confidence`` / ``self._mode`` and use
    :meth:`use` to invoke tools (which records each call for observability). The base class
    handles timing, error capture, and writing the result to working memory.
    """

    name: str = "agent"
    critical: bool = False  # if True, a failure forces human review (EC-04)

    def __init__(self, platform: "Platform") -> None:
        self.platform = platform

    # ---- helpers available to subclasses --------------------------------
    @property
    def llm(self):
        return self.platform.llm

    @property
    def store(self):
        return self.platform.store

    @property
    def domain(self):
        return self.platform.domain

    def use(self, tool_name: str, **kwargs: Any) -> Any:
        """Run a registered tool, record the call, return its output."""
        if not hasattr(self, "_calls"):
            self._calls = []  # allow tool use from helper methods called outside run()
        call = self.platform.tools.get(tool_name).run(**kwargs)
        self._calls.append(call.__dict__)
        return call.output

    def fallback(self) -> None:
        self._mode = "fallback"

    # ---- lifecycle ------------------------------------------------------
    def run(self, session: "Session") -> AgentResult:
        self._calls: list[dict[str, Any]] = []
        self._mode = "tool"
        self._confidence = 0.0
        start = time.perf_counter()
        status = "completed"
        try:
            output = self._run(session) or {}
        except Exception as exc:  # an agent must never crash the platform
            output = {"error": f"{type(exc).__name__}: {exc}"}
            status = "failed"
        latency = int((time.perf_counter() - start) * 1000)
        result = AgentResult(
            name=self.name, output=output, confidence=round(self._confidence, 3),
            status=status, latency_ms=latency, mode=self._mode, tool_calls=self._calls,
        )
        session.record_agent(result)
        return result

    def _run(self, session: "Session") -> dict[str, Any]:  # pragma: no cover - abstract
        raise NotImplementedError
