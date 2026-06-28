"""Notification Agent — customer-facing comms (dispatch, resolution) + engineer alerts.

Not part of the main pipeline; the orchestrator and Execution agent call its ``send_*``
methods directly. All sends go through the simulated ``notify`` tool, so they are safe and
observable but never hit a real provider.
"""
from __future__ import annotations

from typing import Any

from .base import Agent


class NotificationAgent(Agent):
    name = "notification"

    def _run(self, session) -> dict[str, Any]:  # not used in the pipeline
        return {}

    # ---- customer: resolution -------------------------------------------
    def send_resolution_email(self, session) -> dict[str, Any]:
        if session.mem.get_state("resolution_email_sent"):
            return {"skipped": "already_sent"}
        ctx = session.mem.get_context()
        customer = ctx.get("customer_profile") or {}
        exec_result = session.mem.get_blob("exec_result") or {}
        work_update = session.mem.get_blob("work_update") or {}
        review = session.mem.get_blob("human_review") or {}
        candidates = session.mem.get_candidates()
        action = next((c for c in candidates if c["id"] == review.get("selected_action")),
                      candidates[0] if candidates else {})

        customer_name = customer.get("name", "Valued Customer")
        asset_id = session.event.asset_id or "your service"
        action_type = action.get("action_type", "maintenance action")
        action_desc = action.get("description", "The required work has been completed.")
        engineer_name = work_update.get("engineer_name", "Our field team")
        wo_id = exec_result.get("work_order_id", "N/A")

        body = self._generate_email(customer_name, asset_id, action_type, action_desc, engineer_name, wo_id)
        result = self.use("notify", audience=f"customer:{customer.get('id', 'unknown')}", count=1,
                          channel="email", subject=f"Your service issue has been resolved — {asset_id}", body=body)
        session.mem.set_state("resolution_email_sent", True)
        session.log("Resolution email sent", detail=f"to customer {customer.get('id')} for {asset_id}",
                    data={"notification_id": result.get("notification_id"), "channel": "email"})

        intent = session.mem.get_state("intent", {}) or {}
        if intent.get("urgency_tier") in {"P1", "P2"}:
            sms = (f"[EnergyPlatform] Your issue with {asset_id} has been resolved by {engineer_name}. "
                   f"Case #{wo_id}. Reply SUPPORT if you need further help.")
            self.use("notify", audience=f"customer:{customer.get('id', 'unknown')}", count=1, channel="sms", body=sms)
            session.log("Resolution SMS sent", detail="SMS for P1/P2 urgency")
        return result

    # ---- customer: dispatch ---------------------------------------------
    def send_work_assigned_email(self, session) -> dict[str, Any]:
        ctx = session.mem.get_context()
        customer = ctx.get("customer_profile") or {}
        exec_result = session.mem.get_blob("exec_result") or {}
        asset_id = session.event.asset_id or "your service"
        wo_id = exec_result.get("work_order_id", "N/A")
        eta = exec_result.get("eta_min", 45)
        body = self._generate_dispatch_email(customer.get("name", "Valued Customer"), asset_id, wo_id, eta)
        result = self.use("notify", audience=f"customer:{customer.get('id', 'unknown')}", count=1,
                          channel="email", subject=f"We're working on your issue — {asset_id}", body=body)
        session.log("Dispatch email sent", detail=f"ETA {eta}min, WO {wo_id}")
        return result

    # ---- engineer assignment --------------------------------------------
    def send_engineer_assigned_notification(self, session, engineer_name: str) -> None:
        exec_result = session.mem.get_blob("exec_result") or {}
        wo_id = exec_result.get("work_order_id", "")
        asset_id = session.event.asset_id or "unknown asset"
        candidates = session.mem.get_candidates()
        action = candidates[0] if candidates else {}
        self.use("notify", audience=f"engineer:{engineer_name}", count=1, channel="push",
                 subject=f"New work order: {wo_id}",
                 body=(f"Work Order {wo_id} assigned.\nAsset: {asset_id}\n"
                       f"Action: {action.get('action_type', 'maintenance')}\n"
                       f"Priority: {session.mem.get_state('intent', {}).get('urgency_tier', 'P3')}"))
        session.log("Engineer assignment notification", detail=f"{engineer_name} → WO {wo_id}")

    # ---- body generators -------------------------------------------------
    def _generate_email(self, customer_name, asset_id, action_type, action_desc, engineer_name, wo_id) -> str:
        if self.llm.available:
            out = self.llm.complete(
                system=("You are a professional B2B energy customer service rep. Write a warm, concise "
                        "resolution email. Professional but human. Max 150 words. No excessive exclamation marks."),
                user=(f"Customer: {customer_name}. Asset: {asset_id}. Action completed: {action_type} — "
                      f"{action_desc}. Engineer: {engineer_name}. Work order: {wo_id}. Email body only."),
                fast=True)
            if out:
                return out.strip()
        return self._template_resolution_email(customer_name, asset_id, action_type, action_desc, engineer_name, wo_id)

    def _generate_dispatch_email(self, customer_name, asset_id, wo_id, eta) -> str:
        if self.llm.available:
            out = self.llm.complete(
                system="Write a brief, warm 'we are on our way' email. Max 80 words. Professional tone.",
                user=f"Customer: {customer_name}. Issue with: {asset_id}. ETA: {eta} minutes. WO: {wo_id}.",
                fast=True)
            if out:
                return out.strip()
        return (f"Dear {customer_name},\n\nWe have received your report regarding {asset_id} and our team is "
                f"on the way. Estimated arrival: {eta} minutes. Work Order: {wo_id}.\n\nWe will update you once "
                "the work is complete.\n\nEnergy Platform Team")

    @staticmethod
    def _template_resolution_email(customer_name, asset_id, action_type, action_desc, engineer_name, wo_id) -> str:
        return (f"Dear {customer_name},\n\nWe are pleased to inform you that the issue with {asset_id} has been "
                f"resolved.\n\nAction taken: {action_desc}\nCompleted by: {engineer_name}\n"
                f"Work Order reference: {wo_id}\n\nIf you experience any further issues, please contact us.\n\n"
                "Best regards,\nEnergy Operations Team")
