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

        # Otherwise: awaiting human. Record routing + escalation plan + required approver role.
        routing = self._routing(urgency)
        session.mem.set_blob("routing", routing)
        session.mem.set_state("required_role", routing["required_role"])
        session.mem.set_blob("human_review", HumanReview(decision="pending").model_dump())
        session.mem.set_state("awaiting_human", True)
        self._confidence = top.get("confidence", 0.0)
        return {"decision": "pending", "auto_approved": False, "routing": routing,
                "required_role": routing["required_role"]}

    def _auto_approvable(self, session, top, risk_blob, urgency) -> bool:
        """Auto-approve is ONLY eligible for P3/P4, low-risk, whitelisted, safety≈0 actions."""
        from ..auth import AUTO_APPROVE_ROLES

        if session.mem.get_state("force_human_review") or session.mem.get_context().get("cold_start"):
            return False
        # P1/P2 NEVER auto-approve regardless of confidence/risk — safety policy.
        if urgency in {"P1", "P2"} or urgency not in AUTO_APPROVE_ROLES:
            return False
        s = self.platform.settings
        agg = risk_blob.get("aggregate") or self._fallback_risk(session, top)
        dims_ok = all(agg.get(k, 1.0) <= s.auto_approve_risk
                      for k in ("financial", "safety", "compliance", "reputational", "operational"))
        # Evaluate the *grounded* confidence (undo the relevance scaling applied for display).
        relevance = session.mem.get_state("relevance_score", 1.0) or 1.0
        factor = 0.5 + 0.5 * relevance
        base_conf = top.get("confidence", 0.0) / factor if factor > 0 else top.get("confidence", 0.0)
        conf_ok = base_conf >= s.auto_approve_confidence
        whitelisted = top["action_type"] in self.domain.AUTO_APPROVE_WHITELIST
        # Safety risk must be effectively zero for a no-human approval.
        safety_ok = agg.get("safety", 1.0) <= 0.15
        return dims_ok and conf_ok and whitelisted and safety_ok

    # ---- dynamic iterative feedback -------------------------------------
    def generate_followup(self, session, decision: dict[str, Any]) -> str:
        """One LLM call: a concise resolution-check question after the human decides."""
        action_type = self._action_type(session, decision)
        raw = session.event.raw_content or session.event.type
        q = self.llm.complete(
            system=("You are a B2B energy operations assistant. Based on the human's decision, "
                    "generate ONE concise follow-up question to check if the problem was resolved "
                    "or if more action is needed. Max 20 words."),
            user=f"Decision: {decision.get('decision')}. Action taken: {action_type}. Problem: {raw}.",
            fast=True,
        )
        question = (q or "").strip() or self._default_followup(session, action_type)
        session.mem.set_blob("followup_question", question)
        return question

    def generate_specific_followup(self, session, decision: dict[str, Any]) -> str:
        """One LLM call: a more specific diagnostic question when the problem persists."""
        action_type = self._action_type(session, decision)
        raw = session.event.raw_content or session.event.type
        q = self.llm.complete(
            system="You are a B2B energy ops assistant helping diagnose a persistent problem.",
            user=(f"Problem not yet resolved. Original issue: {raw}. Action taken: {action_type}. "
                  "What specific detail should the operator check next? Max 25 words."),
            fast=True,
        )
        question = (q or "").strip() or (
            f"Confirm the {action_type} completed and check the latest telemetry and work-order "
            f"status for {session.event.asset_id or 'the asset'}.")
        session.mem.set_blob("followup_question", question)
        return question

    def _action_type(self, session, decision: dict[str, Any]) -> str:
        sel = decision.get("selected_action")
        for c in session.mem.get_candidates():
            if c["id"] == sel:
                return c["action_type"]
        cands = session.mem.get_candidates()
        return cands[0]["action_type"] if cands else "the recommended action"

    def _default_followup(self, session, action_type: str) -> str:
        return f"Has the issue on {session.event.asset_id or 'the asset'} been resolved after {action_type}?"

    def _fallback_risk(self, session, top) -> dict[str, float]:
        """Deterministic risk for the top action when the Risk Agent was lazily skipped."""
        intent = session.mem.get_state("intent", {}) or {}
        asset = session.mem.get_context().get("asset_record") or {}
        ctx = {"customer_impact_score": intent.get("customer_impact_score", 0.0),
               "severity": session.event.severity, "hv": "HV" in str(asset.get("metadata", ""))}
        try:
            return self.domain.risk_for(top["action_type"], ctx)
        except Exception:
            return {k: 0.9 for k in ("financial", "safety", "compliance", "reputational", "operational")}

    def _routing(self, urgency: str) -> dict[str, Any]:
        from ..auth import HITL_APPROVAL_MATRIX

        required_role = HITL_APPROVAL_MATRIX.get(urgency, "operator")
        routing_by_urgency = {
            "P1": {"required_role": required_role,
                   "channels": ["manager_dashboard", "on_call_manager(SMS+Teams)", "field_engineer_lead(Teams)"],
                   "sla_seconds": 180,
                   "escalation_tiers": [("manager", 180), ("admin", 300), ("emergency_override", 600)],
                   "reason": "Safety-critical action requires manager+ approval"},
            "P2": {"required_role": required_role,
                   "channels": ["manager_dashboard", "operator_dashboard"],
                   "sla_seconds": 900,
                   "escalation_tiers": [("manager", 900), ("admin", 1800)],
                   "reason": "High-impact action requires manager+ approval"},
        }
        if urgency in routing_by_urgency:
            return routing_by_urgency[urgency]
        return {"required_role": required_role,
                "channels": ["operator_dashboard"],
                "sla_seconds": {"P3": 3600, "P4": 14400}.get(urgency, 3600),
                "escalation_tiers": [("operator", 3600)],
                "reason": "Operator-level approval sufficient for this urgency"}
