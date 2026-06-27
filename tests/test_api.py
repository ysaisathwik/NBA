"""API smoke test: event → live trace → human decision → resolution."""
from __future__ import annotations

import os
import time

os.environ.setdefault("NBA_FORCE_OFFLINE", "1")

from fastapi.testclient import TestClient

from nba_platform.api import app as api_module


def _wait(client, sid, predicate, timeout=8.0):
    deadline = time.time() + timeout
    snap = {}
    while time.time() < deadline:
        snap = client.get(f"/api/sessions/{sid}").json()
        if predicate(snap):
            return snap
        time.sleep(0.05)
    return snap


def test_api_end_to_end_with_human_decision():
    client = TestClient(api_module.app)

    scenarios = client.get("/api/scenarios").json()
    transformer = scenarios[0]["event"]
    sid = client.post("/api/events", json=transformer).json()["session_id"]

    # wait until the platform is awaiting a human decision
    snap = _wait(client, sid, lambda s: s.get("awaiting_human"))
    assert snap.get("awaiting_human") is True
    assert snap["candidates"], "recommendations should be present at review time"
    top_id = snap["candidates"][0]["id"]

    # approve with a modification
    r = client.post(f"/api/sessions/{sid}/decision",
                    json={"decision": "modified", "selected_action": top_id,
                          "modifications": ["Notify customers first"], "comments": "go"})
    assert r.json()["ok"] is True

    # wait for resolution
    final = _wait(client, sid, lambda s: s.get("state") in {"CLOSED", "RESOLVED"})
    assert final["state"] in {"CLOSED", "RESOLVED"}
    assert final["verify"]["resolved"] is True


def test_api_auto_approve_needs_no_human():
    client = TestClient(api_module.app)
    event = {"type": "maintenance_due", "source_type": "manual", "asset_id": "TR-442",
             "severity": "INFO",
             "raw_content": "Scheduled preventive maintenance is due for transformer TR-442 per the maintenance plan and asset health review."}
    sid = client.post("/api/events", json=event).json()["session_id"]
    final = _wait(client, sid, lambda s: s.get("state") in {"CLOSED", "RESOLVED"})
    assert final["state"] in {"CLOSED", "RESOLVED"}
    assert final["human_review"]["auto_approved"] is True
