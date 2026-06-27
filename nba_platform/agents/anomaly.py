"""Anomaly Detection Agent — tool-only, deterministic, auditable (no LLM in the path)."""
from __future__ import annotations

from typing import Any

from .base import Agent


class AnomalyAgent(Agent):
    name = "anomaly"

    def _run(self, session) -> dict[str, Any]:
        e = session.event
        sim = session.sim()
        ctx = session.mem.get_context()

        reading = self.use("scada_telemetry", sim=sim) or {}
        stats = self.use("telemetry_stats", sim=sim) or {"mean": 0, "std": 1}
        weather = self.use("weather", lat=sim.get("lat"), lon=sim.get("lon")) or {}

        value = reading.get("value", e.value)
        threshold = reading.get("threshold", e.threshold)

        # EC: stale data → escalate, bypass recommendation.
        if reading.get("stale"):
            session.mem.set_state("telemetry_stale", True)

        detected = False
        z = None
        anomaly_type = "none"
        if value is not None and threshold is not None:
            mean, std = stats.get("mean", value), max(stats.get("std", 1.0), 1e-6)
            z = round((value - mean) / std, 2)
            detected = value > threshold or abs(z) >= 3
            anomaly_type = "thermal_overload" if (e.metric == "temperature") else "threshold_breach"

        duration = int((e.metadata or {}).get("duration_minutes", 47))
        eta = self._predict_eta(value, threshold)
        root_cause = self._root_cause(ctx, e)
        similar = [m.get("id") for m in (ctx.get("episodic_matches") or [])][:3]

        report = {
            "detected": detected, "anomaly_type": anomaly_type, "severity": e.severity,
            "current_value": value, "threshold": threshold, "z_score": z,
            "duration_minutes": duration, "predicted_failure_eta_minutes": eta,
            "root_cause_hypothesis": root_cause, "similar_historical_cases": similar,
            "confidence": 0.94 if detected else 0.6, "stale": bool(reading.get("stale")),
        }
        self._confidence = report["confidence"]
        return report  # persisted by the base class as this agent's output

    def _predict_eta(self, value, threshold) -> int | None:
        if value is None or threshold is None or value <= threshold:
            return None
        # crude time-to-trip: assume trip at 165, degradation ~0.7C/min
        trip = max(threshold + 55, value + 1)
        return int(max(5, (trip - value) / 0.7))

    def _root_cause(self, ctx: dict, e) -> str:
        for t in ctx.get("open_tickets") or []:
            if "fan" in (t.get("type", "") + t.get("metadata", "")).lower():
                return f"cooling fan failure (matches open ticket {t['id']})"
        if e.metric == "temperature":
            return "thermal anomaly — cooling subsystem suspected"
        return "undetermined — requires inspection"
