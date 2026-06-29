"""NotificationAgent — rich HTML email dispatcher for all NexusAgent roles.

Sends:
  - email_case_opened        → customer (on case submission)
  - email_engineer_assigned  → customer (on field-crew dispatch)
  - email_work_done_customer → customer (after work confirmed, awaiting resolution confirm)
  - email_approval_required  → manager/operator (when Gate 1 authorisation is needed)
  - email_task_assigned      → engineer (field work) or operator (processing task)

Emails use the cream HTML template with deep-link CTA buttons. With no provider configured,
they are saved as HTML files in nba_platform/emails/sent_log/ for local preview.
"""
from __future__ import annotations

from typing import Any

from .base import Agent
from ..emails.sender import send_email
from ..emails.templates import (email_approval_required_manager, email_case_opened,
                                email_engineer_assigned, email_task_assigned_engineer,
                                email_task_assigned_operator, email_work_done_customer)


def _get_base_url(session) -> str:
    return getattr(session.platform.settings, "app_base_url", "http://localhost:8000")


def _customer_email(session, customer: dict) -> str:
    # Customer-submitted cases carry the customer's login email in customer_id.
    cid = session.event.customer_id or ""
    if "@" in cid:
        return cid
    return customer.get("email", "")


def _selected_action_type(session) -> str:
    review = session.mem.get_blob("human_review") or {}
    candidates = session.mem.get_candidates()
    action = next((c for c in candidates if c["id"] == review.get("selected_action")),
                  candidates[0] if candidates else {})
    return action.get("action_type", "")


def _send(to_email: str, to_name: str, subject: str, html: str, session, label: str) -> dict:
    if not to_email or "@" not in to_email:
        session.log(f"{label} skipped", detail="no valid email address for recipient")
        return {"skipped": True, "reason": "no_email"}
    result = send_email(to_email=to_email, to_name=to_name, subject=subject, html_body=html)
    session.log(label,
                detail=f"→ {to_email} via {result.get('provider', '?')} | {result.get('local_preview', '')}",
                data=result)
    return result


class NotificationAgent(Agent):
    name = "notification"

    def _run(self, session) -> dict[str, Any]:
        return {}

    # ── 1. CUSTOMER: case opened ─────────────────────────────────────────
    def send_case_opened(self, session) -> dict:
        if session.mem.get_state("case_opened_email_sent"):
            return {"skipped": "already_sent"}
        ctx = session.mem.get_context()
        customer = ctx.get("customer_profile") or {}
        subject, html = email_case_opened(
            recipient_name=customer.get("name", "Valued Customer"),
            case_id=session.sid, event_type=session.event.type, severity=session.event.severity,
            base_url=_get_base_url(session), customer_id=session.event.customer_id or customer.get("id", ""))
        session.mem.set_state("case_opened_email_sent", True)
        return _send(_customer_email(session, customer), customer.get("name", "Customer"),
                     subject, html, session, "Case-opened email (customer)")

    # ── 2. CUSTOMER: engineer dispatched (field work only) ───────────────
    def send_engineer_assigned(self, session) -> dict:
        if session.mem.get_state("engineer_assigned_email_sent"):
            return {"skipped": "already_sent"}
        from ..orchestrator import FIELD_ACTIONS
        if _selected_action_type(session) not in FIELD_ACTIONS:
            return {"skipped": "not_field_work"}
        ctx = session.mem.get_context()
        customer = ctx.get("customer_profile") or {}
        exec_result = session.mem.get_blob("exec_result") or {}
        subject, html = email_engineer_assigned(
            recipient_name=customer.get("name", "Valued Customer"), case_id=session.sid,
            asset_id=session.event.asset_id or "your equipment",
            work_order_id=exec_result.get("work_order_id", "N/A"),
            eta_min=exec_result.get("eta_min", 45), base_url=_get_base_url(session))
        session.mem.set_state("engineer_assigned_email_sent", True)
        return _send(_customer_email(session, customer), customer.get("name", "Customer"),
                     subject, html, session, "Engineer-assigned email (customer)")

    # ── 3. CUSTOMER: work done, please confirm ───────────────────────────
    def send_work_done_customer(self, session) -> dict:
        if session.mem.get_state("work_done_email_sent"):
            return {"skipped": "already_sent"}
        ctx = session.mem.get_context()
        customer = ctx.get("customer_profile") or {}
        exec_result = session.mem.get_blob("exec_result") or {}
        review = session.mem.get_blob("human_review") or {}
        work_update = session.mem.get_blob("work_update") or {}
        candidates = session.mem.get_candidates()
        action = next((c for c in candidates if c["id"] == review.get("selected_action")),
                      candidates[0] if candidates else {})
        subject, html = email_work_done_customer(
            recipient_name=customer.get("name", "Valued Customer"), case_id=session.sid,
            asset_id=session.event.asset_id or "your equipment",
            action_taken=action.get("action_type", "maintenance"),
            engineer_notes=work_update.get("notes", ""),
            work_order_id=exec_result.get("work_order_id", "N/A"), base_url=_get_base_url(session))
        session.mem.set_state("work_done_email_sent", True)
        return _send(_customer_email(session, customer), customer.get("name", "Customer"),
                     subject, html, session, "Work-done email (customer — awaiting confirmation)")

    # ── 4. MANAGER/OPERATOR: approval required ───────────────────────────
    def send_approval_required(self, session) -> dict:
        if session.mem.get_state("approval_email_sent"):
            return {"skipped": "already_sent"}
        from ..auth import HITL_APPROVAL_MATRIX, USERS
        intent = session.mem.get_state("intent", {}) or {}
        urgency = intent.get("urgency_tier", "P4")
        required_role = HITL_APPROVAL_MATRIX.get(urgency, "operator")
        candidates = session.mem.get_candidates()
        top = candidates[0] if candidates else {}
        risk = (session.mem.get_blob("risk") or {}).get("aggregate", {})
        dims = [float(risk.get(k, 0) or 0) for k in
                ("financial", "safety", "compliance", "reputational", "operational")]
        risk_level = ("HIGH" if any(v > 0.6 for v in dims)
                      else "MEDIUM" if any(v > 0.3 for v in dims) else "LOW")
        base = _get_base_url(session)
        results = []
        for email_addr, user_data in USERS.items():
            if user_data["role"] not in {required_role, "admin"}:
                continue
            subject, html = email_approval_required_manager(
                recipient_name=user_data["name"], case_id=session.sid,
                action_type=top.get("action_type", "recommended_action"),
                asset_id=session.event.asset_id or "N/A", urgency=urgency,
                confidence=top.get("confidence", 0.0), risk_level=risk_level, base_url=base)
            results.append(_send(email_addr, user_data["name"], subject, html, session,
                                 f"Approval-required email ({user_data['role']}: {user_data['name']})"))
        session.mem.set_state("approval_email_sent", True)
        return {"sent_to": len(results), "results": results}

    # ── 5. ENGINEER / OPERATOR: task assigned ────────────────────────────
    def send_task_assigned(self, session) -> dict:
        if session.mem.get_state("task_assigned_email_sent"):
            return {"skipped": "already_sent"}
        from ..auth import USERS
        gate2 = session.mem.get_state("gate2_task") or {}
        action_type = gate2.get("action_type", "")
        worker_role = gate2.get("worker_role", "engineer")
        task_desc = gate2.get("task_label", "Complete the assigned work")
        wo_id = gate2.get("work_order_id", "N/A")
        urgency = (session.mem.get_state("intent", {}) or {}).get("urgency_tier", "P4")
        base = _get_base_url(session)
        results = []
        for email_addr, user_data in USERS.items():
            if user_data["role"] != worker_role:
                continue
            if worker_role == "engineer":
                subject, html = email_task_assigned_engineer(
                    recipient_name=user_data["name"], case_id=session.sid, work_order_id=wo_id,
                    asset_id=session.event.asset_id or "N/A", action_type=action_type,
                    task_description=task_desc, urgency=urgency, base_url=base)
            else:
                subject, html = email_task_assigned_operator(
                    recipient_name=user_data["name"], case_id=session.sid, work_order_id=wo_id,
                    action_type=action_type, task_description=task_desc, urgency=urgency, base_url=base)
            results.append(_send(email_addr, user_data["name"], subject, html, session,
                                 f"Task-assigned email ({worker_role}: {user_data['name']})"))
        session.mem.set_state("task_assigned_email_sent", True)
        return {"sent_to": len(results), "results": results}

    # ── Legacy compatibility (keep existing callers working) ─────────────
    def send_resolution_email(self, session) -> dict:
        return self.send_work_done_customer(session)

    def send_work_assigned_email(self, session) -> dict:
        return self.send_engineer_assigned(session)

    def send_engineer_assigned_notification(self, session, engineer_name: str = "") -> None:
        self.send_task_assigned(session)
