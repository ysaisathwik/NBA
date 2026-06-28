"""API tests: auth, guardrails, role-based access, tiered HITL, feedback loop."""
from __future__ import annotations

import os
import time

os.environ.setdefault("NBA_FORCE_OFFLINE", "1")

from fastapi.testclient import TestClient

from nba_platform.api import app as api_module


def client() -> TestClient:
    return TestClient(api_module.app)


def auth(c: TestClient, email: str, password: str) -> dict:
    r = c.post("/api/auth/login", json={"email": email, "password": password})
    assert r.status_code == 200, r.text
    return {"Authorization": "Bearer " + r.json()["token"]}


def _wait(c, sid, headers, predicate, timeout=8.0):
    deadline = time.time() + timeout
    snap = {}
    while time.time() < deadline:
        snap = c.get(f"/api/sessions/{sid}", headers=headers).json()
        if predicate(snap):
            return snap
        time.sleep(0.05)
    return snap


# ---- auth ----------------------------------------------------------------
def test_login_and_me():
    c = client()
    assert c.post("/api/auth/login", json={"email": "x@y.com", "password": "bad"}).status_code == 401
    h = auth(c, "manager@energy.com", "manager123")
    me = c.get("/api/auth/me", headers=h).json()
    assert me["role"] == "manager" and me["permissions"]["can_approve"] is True


def test_protected_endpoints_require_auth():
    c = client()
    assert c.get("/api/sessions").status_code == 401
    assert c.post("/api/events", json={"type": "sensor_alert"}).status_code == 401


# ---- guardrails ----------------------------------------------------------
def test_guardrail_rejects_non_problem_and_short_input():
    c = client()
    h = auth(c, "operator@energy.com", "operator123")
    r1 = c.post("/api/dialogue", json={"raw_dialogue": "No problem"}, headers=h)
    assert r1.status_code == 422 and r1.json()["error"] == "non_problem_input"
    r2 = c.post("/api/dialogue", json={"raw_dialogue": "hello"}, headers=h)
    assert r2.status_code == 422 and r2.json()["rejected"] is True
    r3 = c.post("/api/dialogue", json={"raw_dialogue": "the weather is sunny today and nice"}, headers=h)
    assert r3.status_code == 422 and r3.json()["error"] == "out_of_domain"


def test_guardrail_accepts_real_problem():
    c = client()
    h = auth(c, "operator@energy.com", "operator123")
    r = c.post("/api/dialogue", headers=h, json={
        "raw_dialogue": "The TR-441 transformer is overheating at 142C and 2400 customers affected"})
    assert r.status_code == 200
    assert {"anomaly", "knowledge", "risk"}.issubset(set(r.json()["matched_agents"]))


# ---- role-based access ---------------------------------------------------
def test_customer_cannot_trigger_events_or_list_sessions():
    c = client()
    h = auth(c, "customer@energy.com", "customer123")
    assert c.post("/api/events", json={"type": "sensor_alert"}, headers=h).status_code == 403
    assert c.get("/api/sessions", headers=h).status_code == 403
    assert c.get("/api/my-cases", headers=h).status_code == 200


# ---- tiered HITL ---------------------------------------------------------
def test_operator_cannot_approve_p1_but_manager_can():
    c = client()
    op = auth(c, "operator@energy.com", "operator123")
    mgr = auth(c, "manager@energy.com", "manager123")
    eng = auth(c, "engineer@energy.com", "engineer123")
    transformer = c.get("/api/scenarios").json()[0]["event"]
    sid = c.post("/api/events", json=transformer, headers=op).json()["session_id"]

    snap = _wait(c, sid, op, lambda s: s.get("awaiting_human"))
    assert snap.get("required_role") == "manager"
    # proactive alert (Feature 5) fired for this P1 case
    assert any("Proactive customer alert" in s["title"] for s in snap["trace"])
    top = snap["candidates"][0]["id"]

    # operator is below the required tier for a P1 → 403
    r = c.post(f"/api/sessions/{sid}/decision", headers=op,
               json={"decision": "approved", "selected_action": top})
    assert r.status_code == 403
    # manager has authority → 200
    r = c.post(f"/api/sessions/{sid}/decision", headers=mgr,
               json={"decision": "approved", "selected_action": top})
    assert r.status_code == 200 and r.json()["role"] == "manager"

    # Bug 2: engineer gate — verification waits for a work update
    snap = _wait(c, sid, eng, lambda s: s.get("awaiting_work_update"))
    assert snap.get("awaiting_work_update") is True
    wo = (snap.get("exec_result") or {}).get("work_order_id", "")
    r = c.post(f"/api/sessions/{sid}/work-update", headers=eng,
               json={"work_order_id": wo, "status": "completed", "notes": "fan replaced"})
    assert r.json()["ok"] is True

    # Bug 1: the resolution question targets the CUSTOMER
    snap = _wait(c, sid, mgr, lambda s: s.get("awaiting_feedback"))
    assert snap.get("followup_question")
    assert snap.get("feedback_target") == "customer"
    c.post(f"/api/sessions/{sid}/feedback", json={"resolved": True}, headers=mgr)
    final = _wait(c, sid, mgr, lambda s: s.get("state") == "CLOSED")
    assert final["state"] == "CLOSED"
    # Feature 1: a resolution email was sent; Feature 2: CSAT recorded
    assert any("Resolution email sent" in s["title"] for s in final["trace"])
    assert final.get("csat_score") is not None


def test_work_update_role_enforcement():
    c = client()
    cust = auth(c, "customer@energy.com", "customer123")
    mgr = auth(c, "manager@energy.com", "manager123")
    op = auth(c, "operator@energy.com", "operator123")
    sid = c.post("/api/events", json=c.get("/api/scenarios").json()[0]["event"], headers=op).json()["session_id"]
    # customer and manager cannot submit work updates
    assert c.post(f"/api/sessions/{sid}/work-update", headers=cust, json={"status": "completed"}).status_code == 403
    assert c.post(f"/api/sessions/{sid}/work-update", headers=mgr, json={"status": "completed"}).status_code == 403


def test_engineer_blocked_then_completed():
    c = client()
    mgr = auth(c, "manager@energy.com", "manager123")
    eng = auth(c, "engineer@energy.com", "engineer123")
    sid = c.post("/api/events", json=c.get("/api/scenarios").json()[0]["event"], headers=mgr).json()["session_id"]
    snap = _wait(c, sid, mgr, lambda s: s.get("awaiting_human"))
    c.post(f"/api/sessions/{sid}/decision", headers=mgr,
           json={"decision": "approved", "selected_action": snap["candidates"][0]["id"]})
    snap = _wait(c, sid, eng, lambda s: s.get("awaiting_work_update"))
    wo = (snap.get("exec_result") or {}).get("work_order_id", "")
    # engineer reports a blocker → replan, then completes
    c.post(f"/api/sessions/{sid}/work-update", headers=eng, json={"work_order_id": wo, "status": "blocked", "notes": "no parts"})
    snap = _wait(c, sid, eng, lambda s: s.get("awaiting_work_update"))  # gate re-armed after replan
    c.post(f"/api/sessions/{sid}/work-update", headers=eng, json={"work_order_id": wo, "status": "completed"})
    snap = _wait(c, sid, mgr, lambda s: s.get("awaiting_feedback") or s.get("state") in {"ESCALATED", "CLOSED"})
    assert any("Work blocked" in s["title"] for s in snap["trace"])


def test_p4_billing_auto_approves_without_human():
    c = client()
    op = auth(c, "operator@energy.com", "operator123")
    event = {"type": "billing_dispute", "source_type": "crm", "customer_id": "CUST-Northgate",
             "severity": "INFO",
             "raw_content": "Customer billing dispute: please audit the meter data and issue a corrective invoice adjustment for the disputed charge."}
    sid = c.post("/api/events", json=event, headers=op).json()["session_id"]
    final = _wait(c, sid, op, lambda s: s.get("state") == "CLOSED")
    assert final["human_review"]["auto_approved"] is True
