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


class DialogueParserAgent(Agent):
    name = "dialogue_parser"

    # ------------------------------------------------------------------ run
    def _run(self, session) -> dict[str, Any]:
        """When run inside the pipeline, parse the event's raw_content for the trace."""
        parsed = self.parse(session.event.raw_content)
        session.mem.set_state("matched_agents", parsed["matched_agents"])
        session.mem.set_blob("extracted_event", parsed)
        self._confidence = parsed.get("confidence", 0.7)
        return parsed

    # -------------------------------------------------------------- parsing
    def parse(self, raw_dialogue: str) -> dict[str, Any]:
        """Extract a structured event from free text. Pure — safe to call without a session."""
        parsed = self._parse_llm(raw_dialogue) or self._parse_fallback(raw_dialogue)
        parsed["raw_content"] = raw_dialogue
        parsed["matched_agents"] = self._match_agents(parsed.get("keywords", []), raw_dialogue)
        return parsed

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
