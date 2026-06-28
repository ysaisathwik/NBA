"""Dialogue Parser Agent — turns free-text operator input into a structured Event.

Sits *before* the normal pipeline: it extracts an Event from plain English (LLM with a
deterministic keyword fallback) and maps keywords to the specialist agents that should be
activated. The result is written to session state so the Planner can merge it with its own
deterministic selection.
"""
from __future__ import annotations

import re
from typing import Any

from .base import Agent

# Keyword → specialist-agent mapping (static, domain-tunable).
KEYWORD_AGENT_MAP: dict[str, list[str]] = {
    "temperature": ["anomaly", "knowledge", "risk"],
    "overheating": ["anomaly", "knowledge", "risk"],
    "transformer": ["anomaly", "knowledge"],
    "billing": ["risk"],
    "invoice": ["risk"],
    "outage": ["anomaly", "risk", "knowledge"],
    "customer": ["risk", "explainability"],
    "pressure": ["anomaly"],
    "voltage": ["anomaly", "risk"],
    "maintenance": ["knowledge"],
    "compliance": ["knowledge", "risk"],
    "leak": ["anomaly", "risk", "knowledge"],
    "sensor": ["anomaly"],
    "complaint": ["risk", "explainability"],
}

_EVENT_TYPES = {"sensor_alert", "billing_dispute", "service_outage",
                "customer_complaint", "capacity_alert", "data_anomaly"}

# energy intent → dialogue event_type
_INTENT_TO_EVENT = {
    "equipment_fault": "sensor_alert", "billing_dispute": "billing_dispute",
    "service_outage": "service_outage", "customer_complaint": "customer_complaint",
    "capacity_alert": "capacity_alert", "data_anomaly": "data_anomaly",
    "maintenance_due": "sensor_alert", "regulatory_breach": "service_outage",
    "routine_inquiry": "customer_complaint",
}

_ASSET_RE = re.compile(r"\b([A-Z]{2,3}-?\d{2,4})\b")
_NUM_RE = re.compile(r"(\d[\d,]{1,9})")

# Guardrail vocabularies (Problem 1).
_RESOLUTION_PHRASES = [
    "no problem", "no issue", "all good", "everything is fine", "nothing wrong",
    "all clear", "no fault", "working fine", "resolved", "fixed", "ok now", "good now",
    "never mind", "cancel", "ignore", "test", "hello", "hi ", "hey ",
]
_DOMAIN_KEYWORDS = {
    "transformer", "temperature", "sensor", "voltage", "pressure", "outage", "fault",
    "billing", "invoice", "customer", "maintenance", "leak", "anomaly", "alert",
    "overheating", "circuit", "pump", "turbine", "meter", "grid", "cable", "relay",
    "compliance", "regulation", "complaint", "crew", "dispatch", "ticket", "work order",
    "asset", "equipment", "failure", "warning", "critical", "error", "malfunction",
    "capacity", "demand", "load", "power", "energy", "station", "substation",
}

# Synonym expansion to improve keyword→agent matching (Problem 5c).
KEYWORD_EXPANSION = {
    "transformer": ["TR-", "substation", "winding", "insulation", "oil"],
    "temperature": ["thermal", "heat", "cooling", "overheat", "degrees"],
    "voltage": ["V ", "kV", "surge", "sag", "fluctuation"],
    "billing": ["invoice", "charge", "payment", "statement", "overcharge"],
    "outage": ["blackout", "power cut", "no power", "offline", "down"],
    "leak": ["spill", "seepage", "discharge", "drip"],
    "pressure": ["PSI", "bar", "gauge", "hydraulic"],
}


class DialogueParserAgent(Agent):
    name = "dialogue_parser"

    # ------------------------------------------------------------------ run
    def _run(self, session) -> dict[str, Any]:
        """When run inside the pipeline, parse the event's raw_content for the trace."""
        parsed = self.parse(session.event.raw_content)
        if parsed.get("rejected"):
            session.mem.set_state("matched_agents", [])
            session.mem.set_blob("extracted_event", parsed)
            self._confidence = 0.0
            return parsed
        session.mem.set_state("matched_agents", parsed["matched_agents"])
        session.mem.set_blob("extracted_event", parsed)
        self._confidence = parsed.get("confidence", 0.7)
        return parsed

    # ----------------------------------------------------------- guardrails
    def _validate_input(self, raw: str) -> dict | None:
        """Returns a rejection reason dict if input should be blocked, None if valid."""
        if not raw or not raw.strip():
            return {"rejected": True, "reason": "empty_input", "message": "Please describe your problem."}

        stripped = raw.strip()

        # Too short to be a real problem description.
        if len(stripped) < 10:
            return {"rejected": True, "reason": "too_short",
                    "message": "Please provide more detail about your problem (at least a sentence)."}

        # Gibberish: needs at least 2 alphabetic words.
        words = [w.lower() for w in re.findall(r"[a-zA-Z]+", stripped)]
        if len(words) < 2:
            return {"rejected": True, "reason": "no_content",
                    "message": "Input doesn't contain enough text to process. Describe the issue in plain English."}

        # Negation / resolution phrases — user is saying everything is fine.
        low = stripped.lower()
        for phrase in _RESOLUTION_PHRASES:
            if low.startswith(phrase) or low == phrase.rstrip():
                return {"rejected": True, "reason": "non_problem_input",
                        "message": f"It looks like you're saying everything is fine ('{stripped[:40]}'). "
                                   "Submit a problem only when you need help resolving an issue."}

        # Domain relevance: must mention at least one energy/ops/B2B keyword.
        if not any(kw in low for kw in _DOMAIN_KEYWORDS):
            return {"rejected": True, "reason": "out_of_domain",
                    "message": "This doesn't appear to be an energy operations issue. "
                               "Describe equipment faults, billing disputes, outages, or maintenance needs."}

        return None  # passed all checks

    # -------------------------------------------------------------- parsing
    def parse(self, raw_dialogue: str) -> dict[str, Any]:
        """Extract a structured event from free text. Pure — safe to call without a session."""
        rejection = self._validate_input(raw_dialogue)
        if rejection:
            return rejection  # caller MUST check for "rejected": True
        parsed = self._parse_llm(raw_dialogue) or self._parse_fallback(raw_dialogue)
        parsed["raw_content"] = raw_dialogue
        parsed["keywords"] = self._expand_keywords(parsed.get("keywords", []), raw_dialogue)
        parsed["matched_agents"] = self._match_agents(parsed["keywords"], raw_dialogue)
        return parsed

    def _expand_keywords(self, keywords: list[str], raw: str) -> list[str]:
        """Expand keywords using domain synonyms to improve agent matching."""
        low = (raw or "").lower()
        expanded = list(keywords)
        for kw, synonyms in KEYWORD_EXPANSION.items():
            if any(s.lower() in low for s in synonyms) and kw not in expanded:
                expanded.append(kw)
        return list(dict.fromkeys(expanded))  # dedup preserving order

    def _parse_llm(self, raw: str) -> dict[str, Any] | None:
        out = self.llm.complete(
            system=("You parse B2B energy operations problem reports into structured JSON. "
                    "Be strict and concise."),
            user=(
                f"Operator report: {raw!r}\n\n"
                "Return JSON with exactly these keys: "
                f"event_type (one of {sorted(_EVENT_TYPES)}), "
                "asset_id (string like 'TR-441' or null), "
                "severity (CRITICAL|MAJOR|WARNING|INFO), "
                "keywords (list of lowercase domain keywords found), "
                "customer_count (integer or null)."
            ),
            fast=True, json_mode=True,
        )
        if not isinstance(out, dict) or "event_type" not in out:
            return None
        self._mode = "llm"
        event_type = out.get("event_type")
        if event_type not in _EVENT_TYPES:
            event_type = "data_anomaly"
        return {
            "event_type": event_type,
            "asset_id": out.get("asset_id") or None,
            "severity": (out.get("severity") or "WARNING").upper(),
            "keywords": [k.lower() for k in (out.get("keywords") or [])],
            "customer_count": _coerce_int(out.get("customer_count")),
            "confidence": 0.85,
        }

    def _parse_fallback(self, raw: str) -> dict[str, Any]:
        """Deterministic regex/keyword extraction (EC-05 offline path)."""
        self.fallback()
        low = (raw or "").lower()
        clf = self.use("keyword_classifier", text=raw) or {}
        intent = clf.get("intent", "routine_inquiry")
        event_type = _INTENT_TO_EVENT.get(intent, "data_anomaly")

        asset_match = _ASSET_RE.search(raw or "")
        asset_id = asset_match.group(1) if asset_match else None

        severity = "INFO"
        if any(w in low for w in ("critical", "fire", "explos", "urgent", "immediately", "rising fast", "overheat")):
            severity = "CRITICAL"
        elif any(w in low for w in ("major", "outage", "down", "leak", "fast", "high")):
            severity = "MAJOR"
        elif any(w in low for w in ("warning", "unusual", "anomal", "drift", "elevated")):
            severity = "WARNING"

        customer_count = None
        if "customer" in low or "affected" in low:
            nums = [int(n.replace(",", "")) for n in _NUM_RE.findall(raw or "")]
            big = [n for n in nums if n >= 10]  # ignore small numbers like temperatures
            if big:
                customer_count = max(big)

        keywords = [kw for kw in KEYWORD_AGENT_MAP if kw in low]
        return {
            "event_type": event_type, "asset_id": asset_id, "severity": severity,
            "keywords": keywords, "customer_count": customer_count,
            "confidence": clf.get("confidence", 0.6),
        }

    @staticmethod
    def _match_agents(keywords: list[str], raw: str) -> list[str]:
        """Union of agents for every matched keyword, deduped, order-preserving."""
        low = (raw or "").lower()
        ordered: list[str] = []
        seen: set[str] = set()
        for kw, agents in KEYWORD_AGENT_MAP.items():
            if kw in keywords or kw in low:
                for a in agents:
                    if a not in seen:
                        seen.add(a)
                        ordered.append(a)
        return ordered


def _coerce_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
