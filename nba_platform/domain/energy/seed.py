"""Seed the long-term store with energy-domain data so the platform has context to reason over."""
from __future__ import annotations

from ...memory.longterm import LongTermStore


def seed(store: LongTermStore) -> None:
    # ---- assets ---------------------------------------------------------
    store.upsert("assets", {
        "id": "TR-441", "name": "Transformer TR-441", "type": "transformer",
        "location": "Northgate Substation", "scada_id": "SCADA-TR-441",
        "installation_date": "2012-04-11", "last_maintenance": "2025-10-20",
        "health_score": 0.62, "serves_customers": 2400, "lat": 51.51, "lon": -0.13,
        "metadata": '{"voltage_class": "HV", "age_years": 14}',
    })
    store.upsert("assets", {
        "id": "TR-442", "name": "Transformer TR-442", "type": "transformer",
        "location": "Northgate Substation", "scada_id": "SCADA-TR-442",
        "installation_date": "2018-06-01", "last_maintenance": "2026-03-02",
        "health_score": 0.88, "serves_customers": 1800, "lat": 51.51, "lon": -0.13,
        "metadata": '{"voltage_class": "HV", "age_years": 8}',
    })

    # ---- customers ------------------------------------------------------
    store.upsert("customers", {
        "id": "CUST-Northgate", "name": "Northgate Residential Grid", "tier": "gold",
        "contract": "SLA-99.95", "sentiment": 0.62, "metadata": "{}",
    })

    # ---- open ticket (the unresolved cooling-fan issue) -----------------
    store.upsert("tickets", {
        "id": "T-8821", "asset_id": "TR-441", "type": "cooling_fan_issue", "severity": "MAJOR",
        "status": "OPEN", "created_at": "2026-06-06T09:00:00Z", "resolved_at": None,
        "resolution_summary": None, "metadata": '{"note": "cooling fan intermittent for 3 weeks"}',
    })

    # ---- episodic memory (the precedent case) ---------------------------
    from ...schemas import Event

    fp = Event(type="sensor_alert", asset_id="TR-441", metric="temperature", severity="CRITICAL").fingerprint()
    store.add_episodic({
        "id": "case-2024-11-03",
        "event_fingerprint": fp,
        "context_summary": (
            "Transformer TR-441 overheating alert, temperature 139C above 110C threshold, "
            "cooling fan failure root cause. Resolved by emergency fan replacement with load "
            "transfer to TR-442. Full thermal restoration in 3.2 hours, no outage."),
        "actions_taken": [{"action_type": "emergency_fan_replacement", "outcome": "success"}],
        "outcome": "success", "confidence_delta": 0.02,
    })
    # a second, less-similar case to make episodic retrieval realistic
    store.add_episodic({
        "id": "case-2025-02-18",
        "event_fingerprint": Event(type="sensor_alert", asset_id="TR-442", metric="temperature", severity="MAJOR").fingerprint(),
        "context_summary": (
            "TR-442 thermal warning during heatwave, resolved via controlled load reduction "
            "and coolant injection. No fan fault."),
        "actions_taken": [{"action_type": "load_reduction_coolant", "outcome": "success"}],
        "outcome": "success", "confidence_delta": 0.01,
    })

    # ---- semantic knowledge base (SOPs, standards, manuals) -------------
    knowledge = [
        ("IEC-60076-7.3", "IEC 60076 section 7.3: transformer thermal protection. Continuous "
         "overload above rated top-oil temperature accelerates insulation ageing; sustained "
         "operation above 110C requires load reduction or isolation within defined limits."),
        ("SOP-TH-201", "Maintenance SOP TH-201 emergency cooling procedure: on cooling-fan "
         "failure, transfer load to a neighbouring transformer, dispatch crew for fan "
         "replacement, and monitor top-oil temperature every 60 seconds until below threshold."),
        ("MANUAL-TR441-thermal", "TR-441 maintenance manual, thermal management section: rated "
         "top-oil temperature 105C, alarm at 110C, trip at 165C. Cooling fans model CF-12 are "
         "field-replaceable within 90 minutes by a qualified crew."),
        ("REG-grid-reliability", "Grid reliability regulation GR-14: loss of supply to >2000 "
         "residential customers must be reported within 30 minutes; preventive action required "
         "when failure ETA is under 3 hours."),
        ("SOP-billing-adjust", "Billing dispute SOP: audit interval meter data, compare to "
         "billed usage, and issue corrective adjustment within one business day if variance >5%."),
    ]
    for src, text in knowledge:
        store.add_chunk("knowledge", src, text, {"doc": src})

    # asset/customer history chunks for the semantic tier
    store.add_chunk("history", "TR-441", "TR-441 has an open cooling-fan ticket T-8821 unresolved for 3 weeks; health score 0.62; 14 years old.", {"asset_id": "TR-441"})

    # ---- learning patterns (prior success rates) -----------------------
    store.upsert_pattern({
        "pattern_hash": "equipment_fault|transformer|CRITICAL",
        "feature_vector": {"intent": "equipment_fault", "asset_type": "transformer", "severity": "CRITICAL"},
        "recommended_action": "emergency_fan_replacement",
        "success_rate": 0.87, "sample_count": 23,
    })
