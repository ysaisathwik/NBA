"""Human-in-the-Loop Agent — auto-approve path + review package + escalation routing."""
from __future__ import annotations

from typing import Any

from .base import Agent
from ..schemas import HumanReview

_URGENCY_RANK = {"P1": 1, "P2": 2, "P3": 3, "P4": 4}


class HITLAgent(Agent):
    name = "hitl"

    def _run(self, session) -> dict[str, Any]:
        candidates = session.mem.get_candidates()
        explain = session.mem.get_blob("explanations", []) or []
        risk_blob = session.mem.get_blob("risk", {}) or {}
        intent = session.mem.get_state("intent", {}) or {}

        if not candidates:
            session.mem.set_blob("human_review", HumanReview(decision="rejected", comments="no candidates").model_dump())
            self._confidence = 0.0
            return {"decision": "rejected", "reason": "no_candidates"}

        top = candidates[0]
        urgency = intent.get("urgency_tier", "P4")
        auto = self._auto_approvable(session, top, risk_blob, urgency)

        # Build the review package (what a human sees), push to the queue.
        package = {
            "candidates": candidates,
            "explanations": explain,
            "risk": risk_blob.get("aggregate"),
            "urgency": urgency,
            "auto_approve_eligible": auto,
        }
        session.mem.set_blob("review_package", package)

        if auto:
            review = HumanReview(
                decision="approved", selected_action=top["id"], auto_approved=True,
                reviewer_id="auto-approve-engine",
                comments="Auto-approved: high confidence, low risk, low urgency, whitelisted action.")
            session.mem.set_blob("human_review", review.model_dump())
            self.use("audit_write", user_id="system", action="auto_approve", entity_type="recommendation",
                     entity_id=session.sid, payload={"action": top["action_type"]})
            self._confidence = top.get("confidence", 0.9)
            return {"decision": "approved", "auto_approved": True, "selected_action": top["action_type"]}

        # Otherwise: awaiting human. Record routing + escalation plan.
        routing = self._routing(urgency)
        session.mem.set_blob("human_review", HumanReview(decision="pending").model_dump())
        session.mem.set_state("awaiting_human", True)
        self._confidence = top.get("confidence", 0.0)
        return {"decision": "pending", "auto_approved": False, "routing": routing}

    def _auto_approvable(self, session, top, risk_blob, urgency) -> bool:
        if session.mem.get_state("force_human_review") or session.mem.get_context().get("cold_start"):
            return False
        s = self.platform.settings
        agg = risk_blob.get("aggregate", {})
        dims_ok = all(agg.get(k, 1.0) <= s.auto_approve_risk
                      for k in ("financial", "safety", "compliance", "reputational", "operational"))
        conf_ok = top.get("confidence", 0.0) >= s.auto_approve_confidence
        urgency_ok = _URGENCY_RANK.get(urgency, 1) >= 3  # P3 or P4
        whitelisted = top["action_type"] in self.domain.AUTO_APPROVE_WHITELIST
        return dims_ok and conf_ok and urgency_ok and whitelisted

    def _routing(self, urgency: str) -> dict[str, Any]:
        if urgency == "P1":
            channels = ["operator_dashboard", "on_call_manager(SMS+Teams)", "field_engineer_lead(Teams)"]
            return {"channels": channels, "sla_seconds": 180,
                    "escalation_tiers": [("primary_operator", 180), ("on_call_manager", 300), ("emergency_contact", 600)]}
        return {"channels": ["operator_dashboard"], "sla_seconds": {"P2": 900, "P3": 3600, "P4": 14400}.get(urgency, 3600),
                "escalation_tiers": [("primary_operator", 900)]}
