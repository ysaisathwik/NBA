"""FastAPI command-centre: trigger events, watch the live agent trace, review recommendations."""
from __future__ import annotations

import queue
import threading
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

from ..auth import (HITL_APPROVAL_MATRIX, ROLE_RANK, get_permissions, login,
                    verify_token)
from ..config import Settings
from ..orchestrator import Orchestrator
from ..runtime import Platform, Session
from ..schemas import Event, HumanReview

app = FastAPI(title="Intelligent NBA Platform", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],   # tighten to your domain in production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
def health() -> dict:
    return {
        "status": "ok",
        "llm": "live" if platform.llm.available else "offline",
        "store": "supabase" if platform.settings.supabase_configured else "sqlite",
        "sessions_active": len(_runners),
    }

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
        # engineer work-completion gate (Bug 2)
        self.work_complete_event = threading.Event()
        self.awaiting_work_update = False

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

    def submit_feedback(self, resolved: bool, comment: str = "") -> None:
        self.feedback_q.put({"resolved": resolved, "comment": comment})

    def signal_work_complete(self) -> None:
        self.work_complete_event.set()

    def work_update_provider(self, session: Session) -> dict[str, Any]:
        """Blocks until the engineer marks work complete/blocked, or times out (EC-07)."""
        self.awaiting_work_update = True
        got = self.work_complete_event.wait(timeout=7200)  # 2h
        self.work_complete_event.clear()  # re-arm for any subsequent round
        self.awaiting_work_update = False
        if got:
            return session.mem.get_blob("work_update") or {"status": "completed"}
        session.log("Work update timeout",
                    detail="no engineer update in 2h — proceeding with telemetry verification only")
        return {"status": "timeout"}


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
    comment: str = ""


class CsatIn(BaseModel):
    score: int = 5


class WorkUpdateIn(BaseModel):
    work_order_id: str = ""
    status: str = "completed"  # in_progress | completed | blocked
    notes: str = ""
    engineer_id: str = ""


class LoginIn(BaseModel):
    email: str
    password: str


# --------------------------------------------------------------------------- #
# Auth
# --------------------------------------------------------------------------- #
def require_auth(authorization: str = Header(default="")) -> dict:
    token = authorization.replace("Bearer ", "")
    payload = verify_token(token)
    if not payload:
        raise HTTPException(401, "Authentication required")
    payload.setdefault("id", payload.get("sub"))  # normalise id alias
    return payload


def require_role(*roles: str):
    def dep(user: dict = Depends(require_auth)) -> dict:
        if user["role"] not in roles:
            raise HTTPException(403, f"Role '{user['role']}' cannot perform this action")
        return user
    return dep


@app.post("/api/auth/login")
def auth_login(payload: LoginIn) -> dict:
    result = login(payload.email, payload.password)
    if not result:
        raise HTTPException(401, "Invalid credentials")
    return result


@app.get("/api/auth/me")
def auth_me(authorization: str = Header(default="")) -> dict:
    token = authorization.replace("Bearer ", "")
    payload = verify_token(token)
    if not payload:
        raise HTTPException(401, "Invalid or expired token")
    return {**payload, "permissions": get_permissions(payload["role"])}


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
    session.mem.set_state("interactive_mode", True)  # Gate 1 always needs a real human in API mode
    orch = Orchestrator(platform, decision_provider=runner.provider,
                        feedback_provider=runner.feedback_provider,
                        work_update_provider=runner.work_update_provider)
    threading.Thread(target=orch.run_prepared, args=(session,), daemon=True).start()
    return runner


@app.post("/api/events")
def create_event(payload: EventIn,
                 user: dict = Depends(require_role("operator", "manager", "admin"))) -> dict[str, Any]:
    event = Event(**payload.model_dump())
    if not event.customer_id and user["role"] == "customer":
        event.customer_id = user["email"]
    session = platform.new_session(event)
    session.mem.set_state("triggered_by", user["id"])
    session.mem.set_state("originated_by", user["role"])
    _start(session)
    return {"session_id": session.sid}


@app.post("/api/dialogue")
def submit_dialogue(payload: DialogueIn, user: dict = Depends(require_auth)) -> Any:
    raw = payload.raw_dialogue or ""
    parser = platform.agent("dialogue_parser")
    parsed = parser.parse(raw)

    # Guardrail rejection — return 422 with a user-friendly message, do NOT start a session.
    if parsed.get("rejected"):
        return JSONResponse(status_code=422, content={
            "error": parsed["reason"], "message": parsed["message"], "rejected": True})

    # Customers may only file complaints.
    if user["role"] == "customer":
        parsed["event_type"] = "customer_complaint"

    event = Event(
        type=parsed["event_type"], source_type="dialogue", asset_id=parsed.get("asset_id"),
        severity=parsed.get("severity", "WARNING"), raw_content=raw,
        metadata={"customer_count": parsed.get("customer_count")},
    )
    if user["role"] == "customer":
        event.customer_id = user["email"]
    session = platform.new_session(event)
    session.mem.set_state("triggered_by", user["id"])
    session.mem.set_state("originated_by", user["role"])
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
def list_sessions(user: dict = Depends(require_role("operator", "manager", "admin"))) -> list[dict[str, Any]]:
    out = []
    for sid, r in _runners.items():
        intent = r.session.mem.get_state("intent", {}) or {}
        out.append({"session_id": sid, "state": r.session.state.value,
                    "awaiting_human": r.awaiting, "awaiting_feedback": r.awaiting_feedback,
                    "awaiting_work_update": r.awaiting_work_update,
                    "urgency": intent.get("urgency_tier"),
                    "required_role": r.session.mem.get_state("required_role"),
                    "event": r.session.event.model_dump(mode="json")})
    return out


@app.get("/api/my-cases")
def my_cases(user: dict = Depends(require_auth)) -> list[dict[str, Any]]:
    """Cases relevant to the authenticated user (customers see only their own)."""
    out = []
    for sid, r in _runners.items():
        ev = r.session.event
        if user["role"] == "customer" and ev.customer_id != user.get("email"):
            continue
        out.append({
            "session_id": sid, "state": r.session.state.value, "event_type": ev.type,
            "severity": ev.severity, "asset_id": ev.asset_id, "created_at": ev.timestamp,
            "triggered_by": r.session.mem.get_state("triggered_by"),
        })
    return out


@app.get("/api/sessions/{sid}")
def get_session(sid: str, user: dict = Depends(require_auth)) -> dict[str, Any]:
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
    snap["awaiting_work_update"] = runner.awaiting_work_update
    snap["review_package"] = session.mem.get_blob("review_package") if runner.awaiting else None
    # keep these live while the case is in flight (snapshot may be the pre-flush capture)
    if session.result is None:
        snap["followup_question"] = session.mem.get_blob("followup_question")
        snap["matched_agents"] = session.mem.get_state("matched_agents", [])
        snap["extracted_event"] = session.mem.get_blob("extracted_event")
        snap["feedback_target"] = session.mem.get_state("feedback_target", "operator")
        snap["work_update"] = session.mem.get_blob("work_update")
        snap["work_status_update"] = session.mem.get_state("work_status_update")
        snap["csat_score"] = session.mem.get_state("csat_score")
        snap["csat_factors"] = session.mem.get_state("csat_factors")
        snap["gate2_task"] = session.mem.get_state("gate2_task")
        snap["work_confirmed"] = session.mem.get_state("work_confirmed")
        snap["work_confirmed_by"] = session.mem.get_state("work_confirmed_by")
        snap["work_notes_for_customer"] = session.mem.get_state("work_notes_for_customer")
        snap["customer_confirmed_resolution"] = session.mem.get_state("customer_confirmed_resolution")
        snap["customer_resolution_comment"] = session.mem.get_state("customer_resolution_comment")
    return snap


@app.post("/api/sessions/{sid}/decision")
def submit_decision(sid: str, payload: DecisionIn,
                    user: dict = Depends(require_auth)) -> dict[str, Any]:
    runner = _runners.get(sid)
    if not runner:
        raise HTTPException(404, "session not found")
    if not runner.awaiting:
        raise HTTPException(409, "session is not awaiting a human decision")

    # Enforce the approval hierarchy by urgency tier.
    intent = runner.session.mem.get_state("intent", {}) or {}
    urgency = intent.get("urgency_tier", "P4")
    required_role = HITL_APPROVAL_MATRIX.get(urgency, "operator")
    if ROLE_RANK.get(user["role"], 99) > ROLE_RANK.get(required_role, 3):
        raise HTTPException(403,
            f"This {urgency} case requires {required_role} approval or higher. "
            f"Your role ({user['role']}) does not have sufficient authority.")

    review = HumanReview(
        decision=payload.decision, selected_action=payload.selected_action,
        modifications=payload.modifications, comments=payload.comments,
        reviewer_id=user["id"], response_time_ms=0,
    ).model_dump()
    review["approved_by_role"] = user["role"]
    review["approved_by_name"] = user.get("name", "")
    runner.submit(review)
    return {"ok": True, "approved_by": user.get("name"), "role": user["role"]}


@app.post("/api/sessions/{sid}/feedback")
def submit_feedback(sid: str, payload: FeedbackIn, user: dict = Depends(require_auth)) -> dict[str, Any]:
    runner = _runners.get(sid)
    if not runner:
        raise HTTPException(404, "session not found")
    if not runner.awaiting_feedback:
        raise HTTPException(409, "session is not awaiting feedback")
    session = runner.session
    session.mem.set_state("customer_resolution_comment", payload.comment)
    if payload.resolved:
        session.mem.set_state("customer_confirmed_resolution", True)
    runner.submit_feedback(payload.resolved, payload.comment)
    if payload.resolved:
        _record_resolution_signal(session)  # close the learning loop at the UI level
    return {"ok": True, "resolved": payload.resolved}


@app.post("/api/sessions/{sid}/csat")
def submit_csat(sid: str, payload: CsatIn, user: dict = Depends(require_auth)) -> dict[str, Any]:
    """Customer star rating (1-5) after confirming resolution."""
    runner = _runners.get(sid)
    if not runner:
        raise HTTPException(404, "session not found")
    score = max(1, min(5, int(payload.score)))
    csat = round(score / 5, 3)
    runner.session.mem.set_state("csat_score", csat)
    runner.session.mem.set_state("csat_stars", score)
    # The customer usually rates AFTER the case closed (snapshot already frozen) — patch it too.
    if runner.session.result is not None:
        runner.session.result["csat_score"] = csat
        runner.session.result["csat_stars"] = score
    return {"ok": True, "csat_score": csat}


@app.post("/api/sessions/{sid}/work-update")
def submit_work_update(sid: str, payload: WorkUpdateIn, user: dict = Depends(require_auth)) -> dict[str, Any]:
    """Field engineer submits a work status update. 'completed'/'blocked' unblock verification."""
    if user["role"] not in {"engineer", "operator", "admin"}:
        raise HTTPException(403, "Only engineers and operators can submit work updates")
    runner = _runners.get(sid)
    if not runner:
        raise HTTPException(404, "session not found")

    session = runner.session
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()
    session.mem.set_blob("work_update", {
        "work_order_id": payload.work_order_id, "status": payload.status, "notes": payload.notes,
        "engineer_id": user.get("id", payload.engineer_id), "engineer_name": user.get("name", ""),
        "timestamp": now,
    })
    session.log("Engineer work update",
                detail=f"WO {payload.work_order_id} → {payload.status} by {user.get('name', 'engineer')}",
                data={"status": payload.status, "notes": payload.notes})

    if payload.status == "in_progress":
        # Feature 3: push a customer-facing ETA update.
        exec_result = session.mem.get_blob("exec_result") or {}
        remaining = max(5, int(exec_result.get("eta_min", 45)) // 2)
        session.mem.set_state("work_status_update", {
            "status": "in_progress", "engineer_name": user.get("name", "Our engineer"),
            "remaining_eta_min": remaining, "notes": payload.notes, "updated_at": now})
        session.log("Work in progress update", detail=f"~{remaining}min remaining")

    if payload.status in {"completed", "blocked"}:
        runner.signal_work_complete()  # advances the engineer gate

    return {"ok": True, "status": payload.status, "session_id": sid}


def _record_resolution_signal(session: Session) -> None:
    """A confirmed 'Yes, resolved' reinforces the action that led there."""
    intent = session.mem.get_state("intent", {}) or {}
    review = session.mem.get_blob("human_review", {}) or {}
    candidates = session.mem.get_candidates()
    action = next((c["action_type"] for c in candidates if c["id"] == review.get("selected_action")),
                  candidates[0]["action_type"] if candidates else None)
    if not action:
        return
    key = f"{intent.get('primary_intent')}|{session.event.severity}"
    existing = platform.store.get_pattern(key)
    if existing:
        n = int(existing.get("sample_count", 0))
        rate = (float(existing.get("success_rate", 0.5)) * n + 1.0) / (n + 1)
        platform.store.upsert_pattern({
            "pattern_hash": key, "feature_vector": existing.get("feature_vector", "{}"),
            "recommended_action": action, "success_rate": round(rate, 4), "sample_count": n + 1})
    else:
        platform.store.upsert_pattern({
            "pattern_hash": key, "feature_vector": {"key": key}, "recommended_action": action,
            "success_rate": 1.0, "sample_count": 1})


@app.get("/api/metrics")
def metrics(user: dict = Depends(require_auth)) -> dict[str, Any]:
    sessions = list(_runners.values())
    n = len(sessions)
    avg_agents = round(sum(len(r.session.trace) for r in sessions) / n, 1) if n else 0.0
    auto = sum(1 for r in sessions
               if (r.session.result or {}).get("human_review", {}).get("auto_approved"))
    return {
        "episodic_cases": platform.store.count("episodic_memory"),
        "vector_chunks": platform.store.count("embeddings"),
        "audit_entries": platform.store.count("audit_log"),
        "learning_patterns": platform.store.count("learning_patterns"),
        "sessions": n,
        "total_sessions_today": n,
        "avg_agents_per_session": avg_agents,
        "auto_approve_rate_pct": round(100 * auto / n, 1) if n else 0.0,
        "store_backend": "supabase" if platform.settings.supabase_configured else "sqlite",
        "llm_mode": "live" if platform.llm.available else "offline",
    }


def main() -> None:
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8000)


if __name__ == "__main__":
    main()
