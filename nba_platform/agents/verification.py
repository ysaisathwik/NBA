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

        # Non-telemetry events (billing, complaint, callback) carry no metric/threshold on the
        # ORIGINAL event — the sim injects a default threshold, so detect from the event itself.
        e = session.event
        non_telemetry = e.threshold is None and e.value is None and e.metric is None
        if non_telemetry:
            # Execution success only means a work order was created — NOT that the customer's
            # problem is solved. Force gate 3 (customer confirmation).
            telemetry_ok = False
            value = threshold = None
        else:
            telemetry_ok = value is not None and threshold is not None and value <= threshold

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
        reason = ("all sources confirm" if resolved else
                  f"telemetry {value} vs threshold {threshold}; not yet normalised" if not telemetry_ok else
                  "customer impact persists")
        if non_telemetry:
            # Always route to the customer for confirmation; never auto-resolve.
            resolved = False
            partial = True
            reason = "non-telemetry event — awaiting customer confirmation"

        result = VerifyResult(
            resolved=resolved and not partial, partial_resolve=partial, evidence=evidence,
            confidence=0.9 if resolved else 0.5, reason=reason,
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

        # Composite CSAT: survey base, penalised for replans, bonus for fast human response.
        intent = session.mem.get_state("intent", {}) or {}
        iterations = session.mem.get_state("iteration", 0) or 0
        response_time = (session.mem.get_blob("human_review", {}) or {}).get("response_time_ms", 0)
        base = float(survey.get("positive_pct", 73)) / 100
        iteration_penalty = 0.05 * iterations
        speed_bonus = 0.05 if (response_time and response_time < 60000) else 0
        csat = round(max(0.0, min(1.0, base - iteration_penalty + speed_bonus)), 3)
        session.mem.set_state("csat_score", csat)
        session.mem.set_state("csat_factors", {
            "survey_positive_pct": survey.get("positive_pct"), "iterations": iterations,
            "response_time_ms": response_time, "urgency": intent.get("urgency_tier", "P4"),
        })
