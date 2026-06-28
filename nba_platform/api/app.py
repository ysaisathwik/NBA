"""FastAPI command-centre: trigger events, watch the live agent trace, review recommendations."""
from __future__ import annotations

import queue
import threading
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from ..config import Settings
from ..orchestrator import Orchestrator
from ..runtime import Platform, Session
from ..schemas import Event, HumanReview

app = FastAPI(title="Intelligent NBA Platform", version="1.0.0")

# One in-process platform (in-memory store, re-seeded on boot) shared by all requests.
platform = Platform(settings=Settings(db_path=":memory:"))
_STATIC = Path(__file__).parent / "static"


class SessionRunner:
    """Drives one case in a background thread and bridges the HITL decision gate."""

    def __init__(self, session: Session) -> None:
        self.session = session
        self.decision_event = threading.Event()
        self.decision: dict[str, Any] | None = None
        self.awaiting = False
        # iterative-feedback gate (supports multiple rounds → queue)
        self.feedback_q: "queue.Queue[dict[str, Any]]" = queue.Queue()
        self.awaiting_feedback = False

    def provider(self, session: Session, package: dict[str, Any]) -> dict[str, Any]:
        self.awaiting = True
        # Wait for a human via the API; EC-07 emergency override if no one responds.
        got = self.decision_event.wait(timeout=1200)
        self.awaiting = False
        if got and self.decision:
            return self.decision
        candidates = package.get("candidates", [])
        top = candidates[0] if candidates else None
        return HumanReview(
            decision="approved" if top else "rejected",
            selected_action=top["id"] if top else None,
            emergency_override=True, reviewer_id="auto-escalation",
            comments="EC-07: no human response within SLA — emergency override executed.").model_dump()

    def submit(self, decision: dict[str, Any]) -> None:
        self.decision = decision
        self.decision_event.set()

    def feedback_provider(self, session: Session) -> dict[str, Any]:
        self.awaiting_feedback = True
        try:
            fb = self.feedback_q.get(timeout=1200)
        except queue.Empty:
            fb = {"resolved": True}  # no operator response → assume resolved (audit-logged)
        self.awaiting_feedback = False
        return fb

    def submit_feedback(self, resolved: bool) -> None:
        self.feedback_q.put({"resolved": resolved})


_runners: dict[str, SessionRunner] = {}


class EventIn(BaseModel):
    type: str = "sensor_alert"
    source_type: str = "manual"
    asset_id: str | None = None
    customer_id: str | None = None
    metric: str | None = None
    value: float | None = None
    threshold: float | None = None
    severity: str = "INFO"
    raw_content: str = ""


class DecisionIn(BaseModel):
    decision: str = "approved"  # approved | modified | rejected
    selected_action: str | None = None
    modifications: list[str] = []
    comments: str = ""
    reviewer_id: str = "operator"


class DialogueIn(BaseModel):
    raw_dialogue: str = ""


class FeedbackIn(BaseModel):
    resolved: bool = False


@app.get("/")
def index() -> FileResponse:
    return FileResponse(_STATIC / "index.html")


@app.get("/api/scenarios")
def scenarios() -> list[dict[str, Any]]:
    return [
        {"label": "P1 — Transformer TR-441 overheating", "event": {
            "type": "sensor_alert", "source_type": "scada", "asset_id": "TR-441",
            "customer_id": "CUST-Northgate", "metric": "temperature", "value": 142,
            "threshold": 110, "severity": "CRITICAL",
            "raw_content": "Transformer TR-441 temperature 142C exceeds 110C threshold; thermal overload risk."}},
        {"label": "P4 — Billing dispute (auto-approve eligible)", "event": {
            "type": "billing_dispute", "source_type": "crm", "customer_id": "CUST-Northgate",
            "severity": "INFO", "raw_content": "Customer disputes an invoice charge and requests a refund review."}},
        {"label": "Cold start — novel turbine anomaly", "event": {
            "type": "data_anomaly", "source_type": "scada", "asset_id": "WT-19",
            "metric": "blade_harmonic", "value": 8.2, "threshold": 5.0, "severity": "WARNING",
            "raw_content": "Anomalous blade vibration harmonic signature on wind turbine WT-19; no prior records."}},
    ]


def _start(session: Session) -> SessionRunner:
    runner = SessionRunner(session)
    _runners[session.sid] = runner
    orch = Orchestrator(platform, decision_provider=runner.provider,
                        feedback_provider=runner.feedback_provider)
    threading.Thread(target=orch.run_prepared, args=(session,), daemon=True).start()
    return runner


@app.post("/api/events")
def create_event(payload: EventIn) -> dict[str, Any]:
    event = Event(**payload.model_dump())
    session = platform.new_session(event)
    _start(session)
    return {"session_id": session.sid}


@app.post("/api/dialogue")
def submit_dialogue(payload: DialogueIn) -> dict[str, Any]:
    raw = payload.raw_dialogue or ""
    parser = platform.agent("dialogue_parser")
    parsed = parser.parse(raw)
    event = Event(
        type=parsed["event_type"], source_type="dialogue", asset_id=parsed.get("asset_id"),
        severity=parsed.get("severity", "WARNING"), raw_content=raw,
        metadata={"customer_count": parsed.get("customer_count")},
    )
    session = platform.new_session(event)
    # hand the keyword-matched agents to the Planner via session state
    session.mem.set_state("matched_agents", parsed["matched_agents"])
    session.mem.set_blob("extracted_event", parsed)
    session.log("Dialogue parsed",
                detail=f"event={event.type} · severity={event.severity} · matched={parsed['matched_agents']}",
                data=parsed)
    _start(session)
    return {"session_id": session.sid, "extracted_event": parsed,
            "matched_agents": parsed["matched_agents"]}


@app.get("/api/sessions")
def list_sessions() -> list[dict[str, Any]]:
    out = []
    for sid, r in _runners.items():
        out.append({"session_id": sid, "state": r.session.state.value,
                    "awaiting_human": r.awaiting, "event": r.session.event.model_dump(mode="json")})
    return out


@app.get("/api/sessions/{sid}")
def get_session(sid: str) -> dict[str, Any]:
    runner = _runners.get(sid)
    if not runner:
        raise HTTPException(404, "session not found")
    session = runner.session
    # prefer the live snapshot; fall back to the captured result once Redis is flushed
    snap = session.result or session.snapshot()
    snap["trace"] = session.trace
    snap["state"] = session.state.value
    snap["awaiting_human"] = runner.awaiting
    snap["awaiting_feedback"] = runner.awaiting_feedback
    snap["review_package"] = session.mem.get_blob("review_package") if runner.awaiting else None
    # keep these live while the case is in flight (snapshot may be the pre-flush capture)
    if session.result is None:
        snap["followup_question"] = session.mem.get_blob("followup_question")
        snap["matched_agents"] = session.mem.get_state("matched_agents", [])
        snap["extracted_event"] = session.mem.get_blob("extracted_event")
    return snap


@app.post("/api/sessions/{sid}/decision")
def submit_decision(sid: str, payload: DecisionIn) -> dict[str, Any]:
    runner = _runners.get(sid)
    if not runner:
        raise HTTPException(404, "session not found")
    if not runner.awaiting:
        raise HTTPException(409, "session is not awaiting a human decision")
    review = HumanReview(
        decision=payload.decision, selected_action=payload.selected_action,
        modifications=payload.modifications, comments=payload.comments,
        reviewer_id=payload.reviewer_id, response_time_ms=0,
    ).model_dump()
    runner.submit(review)
    return {"ok": True}


@app.post("/api/sessions/{sid}/feedback")
def submit_feedback(sid: str, payload: FeedbackIn) -> dict[str, Any]:
    runner = _runners.get(sid)
    if not runner:
        raise HTTPException(404, "session not found")
    if not runner.awaiting_feedback:
        raise HTTPException(409, "session is not awaiting feedback")
    runner.submit_feedback(payload.resolved)
    return {"ok": True, "resolved": payload.resolved}


@app.get("/api/metrics")
def metrics() -> dict[str, Any]:
    return {
        "episodic_cases": platform.store.count("episodic_memory"),
        "vector_chunks": platform.store.count("embeddings"),
        "audit_entries": platform.store.count("audit_log"),
        "learning_patterns": platform.store.count("learning_patterns"),
        "sessions": len(_runners),
        "llm_mode": "live" if platform.llm.available else "offline",
    }


def main() -> None:
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8000)


if __name__ == "__main__":
    main()
