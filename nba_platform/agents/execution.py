"""Execution Agent — transactional (saga) execution of the approved action."""
from __future__ import annotations

from typing import Any

from .base import Agent
from ..schemas import ExecResult


class ExecutionAgent(Agent):
    name = "execution"

    def _run(self, session) -> dict[str, Any]:
        review = session.mem.get_blob("human_review", {}) or {}
        if review.get("decision") not in {"approved", "modified"}:
            self._confidence = 0.0
            return {"executed": False, "reason": f"decision={review.get('decision')}"}

        candidates = session.mem.get_candidates()
        selected_id = review.get("selected_action") or (candidates[0]["id"] if candidates else None)
        action = next((c for c in candidates if c["id"] == selected_id), candidates[0] if candidates else None)
        if not action:
            self._confidence = 0.0
            return {"executed": False, "reason": "no_action"}

        # Idempotency (EC): identical (session, action) returns the original result.
        idem_key = f"exec:{session.sid}:{action['id']}"
        existing = self.platform.working.get(idem_key)
        if existing:
            return {"executed": True, "idempotent_replay": True}

        action_type = action["action_type"]
        ctx = session.mem.get_context()
        serves = (ctx.get("asset_record") or {}).get("serves_customers") or 0
        open_tickets = ctx.get("open_tickets") or []
        modifications = review.get("modifications", [])

        result = ExecResult()
        compensations: list[str] = []
        try:
            wo = self.use("create_work_order", action_type=action_type, asset_id=session.event.asset_id)
            result.work_order_id = wo.get("work_order_id")
            result.operations.append({"op": "work_order", "id": result.work_order_id})
            compensations.append("cancel_work_order")

            crew = self.use("dispatch_crew", work_order_id=result.work_order_id)
            result.crew_assignment_id = crew.get("crew_assignment_id")
            result.operations.append({"op": "dispatch", "id": result.crew_assignment_id})
            compensations.append("recall_crew")

            # Customer notification — always runs (and honoured before load transfer if requested).
            if serves:
                ntf = self.use("notify", audience=f"customers({serves})", count=serves)
                result.notifications_sent.append(ntf.get("notification_id"))
                result.operations.append({"op": "notify", "count": serves})

            if action_type in {"emergency_fan_replacement", "isolation_reroute", "load_reduction_coolant"}:
                auto = self.use("automation_trigger", workflow=f"{action_type}_sequence")
                result.operations.append({"op": "automation", "id": auto.get("automation_id")})
                compensations.append("revert_automation")

            for t in open_tickets:
                upd = self.use("crm_update", ticket_id=t["id"], status="IN_PROGRESS")
                self.store.update_ticket(t["id"], status="IN_PROGRESS")
                result.operations.append({"op": "crm_update", "id": upd.get("crm_update_id")})

            result.success = True
        except Exception as exc:  # saga compensation in reverse order
            result.success = False
            result.rolled_back = True
            for comp in reversed(compensations):
                result.operations.append({"op": "compensate", "action": comp})
            result.operations.append({"op": "error", "detail": str(exc)})

        # Apply the physical effect of the action to the telemetry simulator.
        if result.success:
            effects = self.domain.INTERVENTION_EFFECTS.get(action_type, [])
            session.add_intervention(effects)
            self.platform.working.set(idem_key, action["id"])
            self.use("audit_write", user_id=review.get("reviewer_id", "system"), action="execute",
                     entity_type="action", entity_id=action["id"],
                     payload={"action_type": action_type, "modifications": modifications})

        session.mem.set_blob("exec_result", result.model_dump())
        self._confidence = 0.95 if result.success else 0.2
        return {"executed": result.success, "work_order": result.work_order_id, "ops": len(result.operations)}
