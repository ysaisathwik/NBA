"""End-to-end + component tests for the NBA platform."""
from __future__ import annotations

from nba_platform.schemas import CaseState, Event


def transformer_event() -> Event:
    return Event(
        type="sensor_alert", source_type="scada", asset_id="TR-441", customer_id="CUST-Northgate",
        metric="temperature", value=142, threshold=110, severity="CRITICAL",
        raw_content="Transformer TR-441 temperature 142C exceeds 110C threshold.",
        metadata={"duration_minutes": 47},
    )


def test_p1_transformer_resolves_via_goal_loop(orch):
    session = orch.run(transformer_event())
    assert session.state == CaseState.CLOSED
    result = session.result
    # top recommendation is the precedent-boosted fan replacement
    assert result["candidates"][0]["action_type"] == "emergency_fan_replacement"
    assert result["candidates"][0]["precedent_case"] == "case-2024-11-03"
    # safety risk blocked auto-approve → went to human
    assert result["human_review"]["auto_approved"] is False
    # goal loop verified resolution
    assert result["verify"]["resolved"] is True
    assert session.iteration >= 1


def test_all_thirteen_agents_engaged(orch):
    session = orch.run(transformer_event())
    agents_seen = {s["agent"] for s in session.trace if s.get("agent")}
    expected = {"context", "intent", "planner", "anomaly", "knowledge", "risk",
                "recommendation", "explainability", "hitl", "execution",
                "verification", "compression", "learning"}
    assert expected.issubset(agents_seen)


def test_lazy_selection_for_low_stakes_billing(orch):
    session = orch.run(Event(type="billing_dispute", source_type="crm",
                             customer_id="CUST-Northgate", severity="INFO",
                             raw_content="Customer disputes a charge and wants a refund."))
    agents_seen = {s["agent"] for s in session.trace if s.get("agent")}
    # anomaly / risk / knowledge are NOT activated for a routine low-risk billing query
    assert "anomaly" not in agents_seen
    assert "risk" not in agents_seen
    assert "recommendation" in agents_seen


def test_billing_is_not_auto_closed_offline(orch, platform):
    # Billing/complaints are no longer auto-approved or auto-resolved: a billing dispute is a
    # non-telemetry event, so verification routes it to gate 3 (customer confirmation) rather
    # than silently closing on execution success.
    session = orch.run(Event(
        type="billing_dispute", source_type="crm", customer_id="CUST-Northgate", severity="INFO",
        raw_content="Customer billing dispute: please audit the meter data and issue a corrective invoice adjustment for the disputed charge."))
    review = session.result["human_review"]
    assert review["auto_approved"] is not True          # a (simulated) human authorised it
    verify = session.result["verify"] or {}
    assert verify.get("resolved") is not True           # never telemetry-auto-resolved
    assert verify.get("partial_resolve") is True        # awaiting customer confirmation path


def test_p1_never_auto_approved(orch):
    session = orch.run(transformer_event())
    assert session.result["human_review"]["auto_approved"] is False
    assert session.result["required_role"] == "manager"


def test_low_relevance_yields_gather_more_information(platform):
    # Construct a session with no grounding context → relevance gate fires.
    ev = Event(type="customer_complaint", source_type="manual", severity="INFO",
               raw_content="general complaint about service")
    session = platform.new_session(ev)
    platform.agent("context").run(session)
    platform.agent("intent").run(session)
    platform.agent("recommendation").run(session)
    rel = session.mem.get_state("relevance_score")
    assert rel is not None
    if rel < 0.20:
        assert session.mem.get_candidates()[0]["action_type"] == "gather_more_information"


def test_confidence_decay_logged(orch):
    session = orch.run(Event(
        type="data_anomaly", source_type="scada", asset_id="WT-19", metric="blade_harmonic",
        value=8.2, threshold=5.0, severity="WARNING",
        raw_content="harmonic vibration anomaly on turbine WT-19 sensor"))
    # this case escalates through replans → decay should have been applied at least once
    assert any("Confidence decay" in s["title"] for s in session.trace)


def test_agent_cost_tracked(orch):
    session = orch.run(transformer_event())
    assert session.result["total_cost_usd"] > 0


def test_cold_start_blocks_auto_approve_and_caps_confidence(orch):
    session = orch.run(Event(
        type="data_anomaly", source_type="scada", asset_id="WT-19", metric="blade_harmonic",
        value=8.2, threshold=5.0, severity="WARNING",
        raw_content="Anomalous blade vibration harmonic signature on wind turbine WT-19; no prior records."))
    ctx = session.result["context"]
    assert ctx["cold_start"] is True
    assert session.result["candidates"][0]["confidence"] <= 0.70
    assert session.result["human_review"]["auto_approved"] is False


def test_episodic_fingerprint_match(platform):
    fp = transformer_event().fingerprint()
    matches = platform.store.find_episodic(fp, "transformer overheating", top_k=4)
    assert matches and matches[0]["id"] == "case-2024-11-03"
    assert matches[0]["similarity"] >= 0.9


def test_learning_updates_pattern(orch, platform):
    before = platform.store.get_pattern("equipment_fault|transformer|CRITICAL")
    orch.run(transformer_event())
    after = platform.store.get_pattern("equipment_fault|transformer|CRITICAL")
    assert after["sample_count"] == before["sample_count"] + 1


def test_memory_compression_flushes_redis(orch, platform):
    session = orch.run(transformer_event())
    # the large working-memory blobs are flushed post-resolution
    remaining = platform.working.keys(f"session:{session.sid}:")
    assert f"session:{session.sid}:context" not in remaining
    assert f"session:{session.sid}:candidates" not in remaining
    # but a new episodic case + chunks were persisted
    assert platform.store.count("episodic_memory") >= 3


def test_explanations_are_evidence_cited(orch):
    session = orch.run(transformer_event())
    exps = session.result["explanations"]
    top = exps[0]
    assert top["citations"]  # at least one validated citation
    assert top["what_if_not_acted"]
    assert 0 < top["confidence"] <= 1


def test_idempotent_execution(orch, platform):
    session = orch.run(transformer_event())
    # the idempotency key for the executed action is registered
    keys = platform.working.keys("exec:")
    assert any(session.sid in k for k in keys)


def test_risk_scores_are_multidimensional(orch):
    session = orch.run(transformer_event())
    risk = session.result["risk"]["per_action"]["emergency_fan_replacement"]
    assert set(risk) == {"financial", "safety", "compliance", "reputational", "operational"}
    assert risk["safety"] >= 0.9  # live HV equipment rule


def test_dialogue_parser_extracts_offline(platform):
    parsed = platform.agent("dialogue_parser").parse(
        "The TR-441 transformer temperature is rising fast and we have 2400 customers affected")
    assert parsed["asset_id"] == "TR-441"
    assert parsed["event_type"] == "sensor_alert"
    assert parsed["severity"] == "CRITICAL"
    assert parsed["customer_count"] == 2400
    assert {"anomaly", "knowledge", "risk"}.issubset(set(parsed["matched_agents"]))


def test_planner_merges_keyword_matched_agents(platform):
    # A low-stakes billing event whose rule-based selection skips risk/anomaly...
    ev = Event(type="billing_dispute", source_type="crm", customer_id="CUST-Northgate",
               severity="INFO", raw_content="invoice dispute")
    session = platform.new_session(ev)
    # ...but the dialogue layer keyword-matched these agents:
    session.mem.set_state("matched_agents", ["risk", "explainability", "anomaly"])
    platform.agent("context").run(session)
    platform.agent("intent").run(session)
    plan = platform.agent("planner").run(session).output
    assert "risk" in plan["analysis_agents"]      # merged in from keywords
    assert "anomaly" in plan["analysis_agents"]
    assert "risk" in plan["keyword_matched"]
    assert "explainability" not in plan["analysis_agents"]  # lives in the fixed pipeline tail


def test_concurrent_event_merges_into_active_session(platform):
    # EC-06: a second event for the same asset injects rather than duplicating.
    from nba_platform.orchestrator import Orchestrator

    session = platform.new_session(transformer_event())
    platform.active_assets = {"TR-441": session}
    orch = Orchestrator(platform)
    returned = orch.run(transformer_event())
    assert returned is session
    assert any("Concurrent event" in s["title"] for s in session.trace)
