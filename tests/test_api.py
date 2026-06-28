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
    transformer = c.get("/api/scenarios").json()[0]["event"]
    sid = c.post("/api/events", json=transformer, headers=op).json()["session_id"]

    snap = _wait(c, sid, op, lambda s: s.get("awaiting_human"))
    assert snap.get("required_role") == "manager"
    top = snap["candidates"][0]["id"]

    # operator is below the required tier for a P1 → 403
    r = c.post(f"/api/sessions/{sid}/decision", headers=op,
               json={"decision": "approved", "selected_action": top})
    assert r.status_code == 403

    # manager has authority → 200
    r = c.post(f"/api/sessions/{sid}/decision", headers=mgr,
               json={"decision": "approved", "selected_action": top})
    assert r.status_code == 200 and r.json()["role"] == "manager"

    snap = _wait(c, sid, mgr, lambda s: s.get("awaiting_feedback"))
    assert snap.get("followup_question")
    c.post(f"/api/sessions/{sid}/feedback", json={"resolved": True}, headers=mgr)
    final = _wait(c, sid, mgr, lambda s: s.get("state") == "CLOSED")
    assert final["state"] == "CLOSED"


def test_p4_billing_auto_approves_without_human():
    c = client()
    op = auth(c, "operator@energy.com", "operator123")
    event = {"type": "billing_dispute", "source_type": "crm", "customer_id": "CUST-Northgate",
             "severity": "INFO",
             "raw_content": "Customer billing dispute: please audit the meter data and issue a corrective invoice adjustment for the disputed charge."}
    sid = c.post("/api/events", json=event, headers=op).json()["session_id"]
    final = _wait(c, sid, op, lambda s: s.get("state") == "CLOSED")
    assert final["human_review"]["auto_approved"] is True
