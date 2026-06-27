"""Planner Agent — dynamic, lazy orchestration brain. Selects the minimum viable agent set."""
from __future__ import annotations

from typing import Any

from .base import Agent

# Fixed generative tail of every case (after analysis agents).
PIPELINE_TAIL = ["recommendation", "explainability", "hitl", "execution", "verification"]


class PlannerAgent(Agent):
    name = "planner"

    def _run(self, session) -> dict[str, Any]:
        intent = session.mem.get_state("intent", {}) or {}
        ctx = session.mem.get_context()
        e = session.event

        analysis = self._select_analysis(intent, ctx, e)
        plan = {
            "analysis_agents": analysis,
            "pipeline": PIPELINE_TAIL,
            "reason": self._reason(intent, analysis, ctx),
            "fallback_used": not self.llm.available,
        }
        # LLM may refine the reasoning narrative (selection stays deterministic for safety).
        narrative = self.llm.complete(
            system="You are the Planner of an agentic NBA platform. Explain the agent selection in one sentence.",
            user=f"Intent={intent}, cold_start={ctx.get('cold_start')}, selected analysis agents={analysis}.",
            fast=True,
        )
        if narrative:
            self._mode = "llm"
            plan["reason"] = narrative.strip()

        session.mem.set_state("plan", plan)
        session.mem.publish({"type": "plan", "plan": plan})
        self._confidence = 0.9
        return plan

    def _select_analysis(self, intent: dict, ctx: dict, e) -> list[str]:
        primary = intent.get("primary_intent", "routine_inquiry")
        urgency = intent.get("urgency_tier", "P4")
        selected: list[str] = []

        has_telemetry = bool(e.asset_id) and (e.metric is not None or e.threshold is not None or e.type == "sensor_alert")
        if has_telemetry:
            selected.append("anomaly")

        need_knowledge = ctx.get("cold_start") or primary in {
            "equipment_fault", "regulatory_breach", "service_outage", "capacity_alert"}
        if need_knowledge:
            selected.append("knowledge")

        # Lazy: skip risk only for genuinely low-stakes routine work (reference §1.1).
        low_stakes = urgency == "P4" and primary in {"billing_dispute", "routine_inquiry"}
        if not low_stakes:
            selected.append("risk")
        return selected

    def _reason(self, intent: dict, analysis: list[str], ctx: dict) -> str:
        return (f"{intent.get('urgency_tier')} {intent.get('primary_intent')} → activating "
                f"{analysis + PIPELINE_TAIL}" + (" (cold start)" if ctx.get("cold_start") else ""))

    # ---- replan decision (goal loop) ------------------------------------
    def replan(self, session, verify: dict) -> dict[str, Any]:
        """Decide what to do after a failed/partial verification."""
        sim = session.sim()
        value = sim.get("baseline")
        interventions = sim.get("interventions", [])
        # Estimate trajectory from the telemetry simulator.
        reading = self.use("scada_telemetry", sim=sim) or {}
        cur = reading.get("value")
        threshold = reading.get("threshold")

        decision = "new_action"
        reason = "no improvement detected — selecting an alternative action"
        next_wait = 5

        if verify.get("partial_resolve"):
            decision, reason, next_wait = "customer_followup", "technical fix confirmed; customer impact persists", 15
        elif cur is not None and threshold is not None and interventions:
            # declining toward threshold within an acceptable window → keep monitoring
            if cur <= threshold:
                decision, reason = "resolved_now", "telemetry normalised on re-check"
            else:
                eta = self._eta_to_safe(sim, cur, threshold)
                if eta is not None and eta <= 40:
                    decision = "continue"
                    reason = f"trajectory acceptable, ETA to safe temp ~{eta}min — continue current plan"
                    next_wait = min(eta + 5, 40)
                else:
                    decision, reason = "new_action", "trajectory too slow — escalate to a stronger action"

        plan = {"decision": decision, "reason": reason, "next_wait_minutes": next_wait}
        session.mem.publish({"type": "replan", "plan": plan})
        return plan

    def _eta_to_safe(self, sim, cur, threshold) -> int | None:
        # find max ramp rate among interventions to project time to threshold
        rates = [iv.get("rate", 0) for iv in sim.get("interventions", []) if iv.get("kind") == "ramp"]
        rate = max(rates, default=0)
        steps = [iv for iv in sim.get("interventions", []) if iv.get("kind") == "step"]
        if steps:
            # a step intervention (e.g. crew fan replacement) will resolve at its delay
            elapsed = sim.get("elapsed_min", 0)
            return int(max(0, min(s.get("delay", 45) for s in steps) - elapsed))
        if rate <= 0:
            return None
        return int(max(0, (cur - threshold) / rate))
