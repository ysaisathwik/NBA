"""Energy domain configuration: taxonomy, action templates, risk rules, planner templates."""
from __future__ import annotations

from typing import Any

NAME = "energy"

# --------------------------------------------------------------------------- #
# Intent taxonomy + keyword classifier (LLM-free fallback)
# --------------------------------------------------------------------------- #
INTENTS = [
    "equipment_fault", "billing_dispute", "service_outage", "maintenance_due",
    "regulatory_breach", "customer_complaint", "capacity_alert", "data_anomaly",
    "routine_inquiry",
]

KEYWORD_MAP: dict[str, list[str]] = {
    "equipment_fault": ["overheat", "temperature", "transformer", "fault", "fan", "vibration",
                        "fire", "smoke", "breaker", "winding", "thermal", "failure"],
    "service_outage": ["outage", "blackout", "power loss", "down", "no power", "interruption"],
    "billing_dispute": ["bill", "invoice", "charge", "overcharge", "refund", "payment", "tariff"],
    "maintenance_due": ["maintenance", "inspection", "service due", "overdue", "scheduled"],
    "regulatory_breach": ["regulation", "compliance", "breach", "violation", "iec", "emission", "permit"],
    "customer_complaint": ["complaint", "unhappy", "frustrated", "angry", "escalate", "dissatisfied"],
    "capacity_alert": ["capacity", "overload", "demand", "peak", "load", "exceed"],
    "data_anomaly": ["anomaly", "spike", "drift", "sensor", "reading", "irregular"],
    "routine_inquiry": ["question", "info", "status", "how", "when", "request"],
}

DOMAIN_TAGS = {
    "equipment_fault": ["safety", "operations"],
    "service_outage": ["operations", "reputational"],
    "billing_dispute": ["billing"],
    "maintenance_due": ["operations"],
    "regulatory_breach": ["regulatory", "compliance"],
    "customer_complaint": ["reputational"],
    "capacity_alert": ["operations", "regulatory"],
    "data_anomaly": ["operations"],
    "routine_inquiry": ["billing"],
}


def classify_keywords(text: str) -> tuple[str, float]:
    """Deterministic regex/TF classifier — the EC fallback when the LLM is unavailable."""
    low = (text or "").lower()
    best, best_hits = "routine_inquiry", 0
    for intent, kws in KEYWORD_MAP.items():
        hits = sum(1 for kw in kws if kw in low)
        if hits > best_hits:
            best, best_hits = intent, hits
    conf = min(0.6 + 0.1 * best_hits, 0.9) if best_hits else 0.4
    return best, conf


# --------------------------------------------------------------------------- #
# Action templates  (intent -> ranked candidate actions)
# --------------------------------------------------------------------------- #
ACTION_TEMPLATES: dict[str, list[dict[str, Any]]] = {
    "equipment_fault": [
        {
            "action_type": "emergency_fan_replacement",
            "description": "Dispatch crew for emergency cooling-fan replacement and transfer 40% load to a neighbouring asset.",
            "base_confidence": 0.91, "estimated_impact": "HIGH", "estimated_duration": "4h",
            "prerequisites": ["crew availability", "spare fan in stock", "neighbouring asset capacity"],
            "resource_requirements": ["field crew", "replacement fan", "load-transfer automation"],
        },
        {
            "action_type": "load_reduction_coolant",
            "description": "Controlled load reduction plus emergency coolant injection to buy thermal headroom.",
            "base_confidence": 0.84, "estimated_impact": "MEDIUM", "estimated_duration": "45min",
            "prerequisites": ["coolant available"], "resource_requirements": ["remote ops", "coolant unit"],
        },
        {
            "action_type": "isolation_reroute",
            "description": "Immediately isolate the transformer and reroute supply through alternate feeders.",
            "base_confidence": 0.78, "estimated_impact": "HIGH", "estimated_duration": "90min",
            "prerequisites": ["alternate feeder capacity"], "resource_requirements": ["switching crew"],
        },
        {
            "action_type": "cooling_boost_monitor",
            "description": "Remote cooling boost and intensified monitoring while a crew is mobilised.",
            "base_confidence": 0.61, "estimated_impact": "LOW-MEDIUM", "estimated_duration": "15min",
            "prerequisites": [], "resource_requirements": ["remote ops"],
        },
    ],
    "service_outage": [
        {"action_type": "isolation_reroute", "description": "Reroute supply through alternate feeders to restore service.",
         "base_confidence": 0.82, "estimated_impact": "HIGH", "estimated_duration": "60min",
         "prerequisites": ["alternate feeder capacity"], "resource_requirements": ["switching crew"]},
        {"action_type": "dispatch_inspection", "description": "Dispatch a crew to inspect and restore the faulted segment.",
         "base_confidence": 0.74, "estimated_impact": "MEDIUM", "estimated_duration": "2h",
         "prerequisites": [], "resource_requirements": ["field crew"]},
    ],
    "billing_dispute": [
        {"action_type": "billing_adjustment", "description": "Review meter data and issue a corrective billing adjustment.",
         "base_confidence": 0.88, "estimated_impact": "MEDIUM", "estimated_duration": "1d",
         "prerequisites": ["meter audit"], "resource_requirements": ["billing analyst"]},
        {"action_type": "schedule_callback", "description": "Schedule an account-manager callback to explain charges.",
         "base_confidence": 0.7, "estimated_impact": "LOW", "estimated_duration": "1d",
         "prerequisites": [], "resource_requirements": ["account manager"]},
    ],
    "capacity_alert": [
        {"action_type": "load_reduction_coolant", "description": "Shed non-critical load and rebalance the feeder.",
         "base_confidence": 0.8, "estimated_impact": "MEDIUM", "estimated_duration": "30min",
         "prerequisites": [], "resource_requirements": ["remote ops"]},
        {"action_type": "schedule_maintenance", "description": "Schedule capacity upgrade assessment.",
         "base_confidence": 0.65, "estimated_impact": "LOW", "estimated_duration": "1w",
         "prerequisites": [], "resource_requirements": ["planning team"]},
    ],
    "maintenance_due": [
        {"action_type": "schedule_maintenance", "description": "Create a scheduled preventive-maintenance work order.",
         "base_confidence": 0.86, "estimated_impact": "MEDIUM", "estimated_duration": "scheduled",
         "prerequisites": [], "resource_requirements": ["maintenance crew"]},
    ],
    "regulatory_breach": [
        {"action_type": "dispatch_inspection", "description": "Dispatch compliance inspection and file corrective report.",
         "base_confidence": 0.8, "estimated_impact": "HIGH", "estimated_duration": "1d",
         "prerequisites": [], "resource_requirements": ["compliance officer"]},
    ],
    "customer_complaint": [
        {"action_type": "schedule_callback", "description": "Proactive account-manager outreach to resolve the complaint.",
         "base_confidence": 0.72, "estimated_impact": "MEDIUM", "estimated_duration": "1d",
         "prerequisites": [], "resource_requirements": ["account manager"]},
    ],
}

# Generic fallback when an intent has no specific template.
GENERIC_TEMPLATE = [
    {"action_type": "dispatch_inspection", "description": "Dispatch an inspection to gather more information.",
     "base_confidence": 0.6, "estimated_impact": "MEDIUM", "estimated_duration": "1d",
     "prerequisites": [], "resource_requirements": ["field crew"]},
    {"action_type": "schedule_callback", "description": "Schedule human follow-up to clarify the request.",
     "base_confidence": 0.55, "estimated_impact": "LOW", "estimated_duration": "1d",
     "prerequisites": [], "resource_requirements": ["operator"]},
]


def action_templates(intent: str, anomaly_type: str | None = None) -> list[dict[str, Any]]:
    return [dict(t) for t in ACTION_TEMPLATES.get(intent, GENERIC_TEMPLATE)]


# --------------------------------------------------------------------------- #
# Risk rules (deterministic, per action)
# --------------------------------------------------------------------------- #
RISK_PROFILE: dict[str, dict[str, float]] = {
    "emergency_fan_replacement": {"financial": 0.72, "safety": 0.91, "compliance": 0.80, "reputational": 0.65, "operational": 0.6},
    "isolation_reroute": {"financial": 0.45, "safety": 0.6, "compliance": 0.7, "reputational": 0.75, "operational": 0.8},
    "load_reduction_coolant": {"financial": 0.25, "safety": 0.4, "compliance": 0.5, "reputational": 0.5, "operational": 0.45},
    "cooling_boost_monitor": {"financial": 0.1, "safety": 0.3, "compliance": 0.35, "reputational": 0.3, "operational": 0.25},
    "billing_adjustment": {"financial": 0.2, "safety": 0.0, "compliance": 0.25, "reputational": 0.3, "operational": 0.1},
    "schedule_callback": {"financial": 0.05, "safety": 0.0, "compliance": 0.1, "reputational": 0.2, "operational": 0.05},
    "schedule_maintenance": {"financial": 0.15, "safety": 0.2, "compliance": 0.2, "reputational": 0.15, "operational": 0.2},
    "dispatch_inspection": {"financial": 0.12, "safety": 0.3, "compliance": 0.3, "reputational": 0.25, "operational": 0.2},
}


def risk_for(action_type: str, context: dict[str, Any]) -> dict[str, float]:
    """Deterministic per-action risk vector, modulated by context."""
    base = dict(RISK_PROFILE.get(action_type, {"financial": 0.4, "safety": 0.4, "compliance": 0.4, "reputational": 0.4, "operational": 0.4}))
    impact = float(context.get("customer_impact_score", 0.0))
    severity = str(context.get("severity", "INFO")).upper()
    hv = bool(context.get("hv", False))
    # High customer impact raises reputational + compliance exposure.
    base["reputational"] = min(1.0, base["reputational"] + 0.2 * impact)
    base["compliance"] = min(1.0, base["compliance"] + 0.1 * impact)
    # Live HV equipment is always at least safety-critical (deterministic rule).
    if hv and action_type in {"emergency_fan_replacement", "isolation_reroute"}:
        base["safety"] = max(base["safety"], 0.9)
    if severity == "CRITICAL":
        base["operational"] = min(1.0, base["operational"] + 0.1)
    return {k: round(v, 2) for k, v in base.items()}


# Only fully-automated, no-impact monitoring actions may ever skip the human authorisation gate.
AUTO_APPROVE_WHITELIST = {"cooling_boost_monitor"}

# Intents that ALWAYS require a human reviewer (and customer confirmation) — never auto-closed.
# Customer submissions are normalised to customer_complaint, so this guarantees a human + a
# customer confirmation for every customer-raised issue (e.g. a "wrong invoice" complaint).
NEVER_AUTO_APPROVE_INTENTS = {"customer_complaint"}


# --------------------------------------------------------------------------- #
# Planner fallback templates (event_type -> agent subset)  +  intervention effects
# --------------------------------------------------------------------------- #
PLANNER_TEMPLATES: dict[str, list[str]] = {
    "sensor_alert": ["context", "intent", "knowledge", "anomaly", "risk", "recommendation", "explainability", "hitl", "execution", "verification"],
    "billing_dispute": ["context", "intent", "recommendation", "explainability", "hitl", "execution", "verification"],
    "customer_complaint": ["context", "intent", "knowledge", "recommendation", "explainability", "hitl", "execution", "verification"],
    "service_outage": ["context", "intent", "anomaly", "risk", "recommendation", "explainability", "hitl", "execution", "verification"],
    "default": ["context", "intent", "recommendation", "explainability", "hitl", "execution", "verification"],
}

# Map an executed action to its effect on the asset telemetry simulator.
INTERVENTION_EFFECTS: dict[str, list[dict[str, Any]]] = {
    "emergency_fan_replacement": [{"kind": "ramp", "rate": 0.8, "delay": 0}, {"kind": "step", "delay": 45}],
    "load_reduction_coolant": [{"kind": "ramp", "rate": 1.6, "delay": 0}],
    "isolation_reroute": [{"kind": "step", "delay": 10}],
    "cooling_boost_monitor": [{"kind": "ramp", "rate": 0.4, "delay": 0}],
}
