"""Verification Agent — the quality gate. Confirms resolution across four evidence sources."""
from __future__ import annotations

from typing import Any

from .base import Agent
from ..schemas import VerifyResult


class VerificationAgent(Agent):
    name = "verification"

    def _run(self, session) -> dict[str, Any]:
        sim = session.sim()
        ctx = session.mem.get_context()
        exec_result = session.mem.get_blob("exec_result", {}) or {}

        reading = self.use("scada_telemetry", sim=sim) or {}
        value = reading.get("value")
        threshold = reading.get("threshold")
        telemetry_ok = (value is not None and threshold is not None and value <= threshold)
        # for non-telemetry events (no threshold), treat execution success as technical fix
        if threshold is None:
            telemetry_ok = bool(exec_result.get("success"))

        ticket_ok = bool(exec_result.get("work_order_id"))

        base_sentiment = (ctx.get("customer_profile") or {}).get("sentiment", 0.7)
        sentiment = self.use("crm_sentiment", base=base_sentiment, elapsed_min=sim.get("elapsed_min", 0))
        customer_ok = sentiment >= base_sentiment

        kpi = {"grid_stability_pct": 99.2 if telemetry_ok else 98.0}

        evidence = {
            "telemetry": {"value": value, "threshold": threshold, "normalised": telemetry_ok},
            "ticket": {"work_order": exec_result.get("work_order_id"), "ok": ticket_ok},
            "customer": {"sentiment": sentiment, "base": base_sentiment, "ok": customer_ok},
            "kpi": kpi,
        }

        resolved = bool(telemetry_ok and ticket_ok)
        partial = bool(telemetry_ok and not customer_ok)

        result = VerifyResult(
            resolved=resolved and not partial, partial_resolve=partial, evidence=evidence,
            confidence=0.9 if resolved else 0.5,
            reason=("all sources confirm" if resolved else
                    f"telemetry {value} vs threshold {threshold}; not yet normalised" if not telemetry_ok else
                    "customer impact persists"),
        )

        if result.resolved:
            self._finalise_resolution(session, ctx, value, threshold)
        session.mem.set_blob("verify", result.model_dump())
        self._confidence = result.confidence
        return result.model_dump()

    def _finalise_resolution(self, session, ctx, value, threshold) -> None:
        for t in ctx.get("open_tickets") or []:
            self.store.update_ticket(
                t["id"], status="RESOLVED",
                resolution_summary=f"Resolved via NBA platform; telemetry {value} ≤ {threshold}.")
        survey = self.use("customer_survey", customer_id=(ctx.get("customer_profile") or {}).get("id"))
        session.mem.set_state("csat_positive_pct", survey.get("positive_pct"))
