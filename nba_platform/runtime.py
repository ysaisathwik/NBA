"""Platform runtime: wires memory tiers, tools, domain, LLM, and agents; owns sessions."""
from __future__ import annotations

from typing import Any, Callable

from .config import Settings, get_settings
from .domain import load_domain, seed_domain
from .llm import get_llm
from .memory.longterm import LongTermStore
from .memory.working import SessionMemory, WorkingMemory
from .schemas import CaseState, Event, new_id, utcnow
from .tools import build_registry
from .tools.simulated import Simulators


class Platform:
    """Top-level handle. One per process (or per tenant)."""

    def __init__(self, settings: Settings | None = None, seed: bool = True) -> None:
        self.settings = settings or get_settings()
        self.working = WorkingMemory()
        self.store = LongTermStore(self.settings.db_path)
        self.sims = Simulators()
        self.domain = load_domain(self.settings.domain)
        self.tools = build_registry(self.store, self.sims, self.domain)
        self.llm = get_llm()
        if seed:
            seed_domain(self.settings.domain, self.store)
        # build agents lazily to avoid an import cycle (agents import Platform typing only)
        from .agents import build_agents

        self.agents = build_agents(self)

    def agent(self, name: str):
        return self.agents[name]

    def new_session(self, event: Event, goal: str | None = None) -> "Session":
        return Session(self, event, goal)


class Session:
    """A single case lifecycle + its working-memory blackboard and observable trace."""

    def __init__(self, platform: Platform, event: Event, goal: str | None = None) -> None:
        self.platform = platform
        self.event = event
        self.sid = new_id("sess")
        self.mem = SessionMemory(platform.working, self.sid)
        self.trace: list[dict[str, Any]] = []
        self.state = CaseState.INITIATED
        self._step = 0
        self.goal = goal or f"Resolve {event.type} on {event.asset_id or 'target'} and prevent escalation"
        self.iteration = 0
        self.result: dict[str, Any] | None = None  # snapshot captured before Redis flush

        self.mem.set_state("event", event.model_dump())
        self.mem.set_state("goal", self.goal)
        self.mem.set_state("status", self.state.value)
        self.mem.set_state("iteration", 0)
        self._init_sim()

    # ---- virtual clock + telemetry simulator ----------------------------
    def _init_sim(self) -> None:
        e = self.event
        asset = self.platform.store.get_asset(e.asset_id) if e.asset_id else None
        threshold = e.threshold if e.threshold is not None else 110.0
        baseline = e.value if e.value is not None else threshold
        sim = {
            "asset_id": e.asset_id,
            "metric": e.metric or "value",
            "baseline": baseline,
            "normal": (threshold - 16) if e.threshold is not None else baseline,
            "threshold": threshold,
            "elapsed_min": 0.0,
            "interventions": [],
            "lat": asset.get("lat") if asset else None,
            "lon": asset.get("lon") if asset else None,
        }
        self.mem.set_blob("sim", sim)

    def sim(self) -> dict[str, Any]:
        return self.mem.get_blob("sim", {})

    def advance_clock(self, minutes: float) -> None:
        sim = self.sim()
        sim["elapsed_min"] = sim.get("elapsed_min", 0.0) + minutes
        self.mem.set_blob("sim", sim)

    def add_intervention(self, effects: list[dict[str, Any]]) -> None:
        sim = self.sim()
        at = sim.get("elapsed_min", 0.0)
        for eff in effects:
            sim.setdefault("interventions", []).append({**eff, "at": at})
        self.mem.set_blob("sim", sim)

    # ---- observability --------------------------------------------------
    def set_state(self, state: CaseState) -> None:
        self.state = state
        self.mem.set_state("status", state.value)
        self.mem.publish({"type": "state", "state": state.value})

    def log(self, title: str, detail: str = "", data: Any = None, agent: str | None = None) -> dict[str, Any]:
        self._step += 1
        entry = {
            "step": self._step, "title": title, "detail": detail, "data": data,
            "agent": agent, "state": self.state.value, "ts": utcnow(),
        }
        self.trace.append(entry)
        self.mem.publish({"type": "trace", **entry})
        return entry

    def record_agent(self, result) -> None:
        self.mem.set_agent(result.name, result.as_dict())
        self.log(
            f"{result.name} agent", detail=f"{result.status} · conf={result.confidence} · {result.latency_ms}ms",
            data={"mode": result.mode, "tool_calls": [c.get("tool") for c in result.tool_calls]},
            agent=result.name,
        )

    def snapshot(self) -> dict[str, Any]:
        return {
            "session_id": self.sid,
            "state": self.state.value,
            "goal": self.goal,
            "iteration": self.iteration,
            "event": self.event.model_dump(mode="json"),
            "trace": self.trace,
            "extracted_event": self.mem.get_blob("extracted_event"),
            "matched_agents": self.mem.get_state("matched_agents", []),
            "followup_question": self.mem.get_blob("followup_question"),
            "candidates": self.mem.get_candidates(),
            "explanations": self.mem.get_blob("explanations", []),
            "context": self.mem.get_context(),
            "intent": self.mem.get_state("intent"),
            "plan": self.mem.get_state("plan"),
            "risk": self.mem.get_blob("risk"),
            "human_review": self.mem.get_blob("human_review"),
            "exec_result": self.mem.get_blob("exec_result"),
            "verify": self.mem.get_blob("verify"),
        }
