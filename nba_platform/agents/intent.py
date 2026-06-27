"""Intent Classification Agent — taxonomy, urgency tier, domains, customer impact."""
from __future__ import annotations

from typing import Any

from .base import Agent
from ..schemas import Intent, Urgency

_SEVERITY_URGENCY = {"CRITICAL": Urgency.P1, "MAJOR": Urgency.P2, "WARNING": Urgency.P3, "INFO": Urgency.P4}


class IntentAgent(Agent):
    name = "intent"
    critical = True

    def _run(self, session) -> dict[str, Any]:
        e = session.event
        ctx = session.mem.get_context()
        text = e.raw_content or f"{e.type} {e.metric or ''} {e.severity}"

        intent = self._classify_llm(text, e) or self._classify_rules(text, e)

        # Deterministic domain rule: CRITICAL SCADA alert is always P1 (overrides model).
        if e.severity.upper() == "CRITICAL" and e.source_type in {"scada", "manual"} and e.type == "sensor_alert":
            intent.urgency_tier = Urgency.P1
            intent.method = intent.method + "+rules_p1"

        # Customer-impact from asset reach.
        serves = (ctx.get("asset_record") or {}).get("serves_customers") or 0
        if serves:
            intent.customer_impact_score = max(intent.customer_impact_score, min(0.95, serves / 2800))

        session.mem.set_state("intent", intent.model_dump(mode="json"))
        self._confidence = intent.confidence
        return intent.model_dump(mode="json")

    # ---- LLM primary -----------------------------------------------------
    def _classify_llm(self, text: str, e) -> Intent | None:
        out = self.llm.complete(
            system="You classify B2B energy operations events. Output strict JSON.",
            user=(
                f"Event: type={e.type}, severity={e.severity}, content={text!r}.\n"
                f"Choose primary_intent from {self.domain.INTENTS}.\n"
                "Return JSON: {primary_intent, urgency_tier (P1-P4), domains (list), "
                "customer_impact_score (0-1), confidence (0-1)}."
            ),
            fast=True, json_mode=True,
        )
        if not isinstance(out, dict) or "primary_intent" not in out:
            return None
        self._mode = "llm"
        try:
            intent = Intent(
                primary_intent=out["primary_intent"],
                urgency_tier=Urgency(out.get("urgency_tier", "P4")),
                domains=out.get("domains") or self.domain.DOMAIN_TAGS.get(out["primary_intent"], []),
                customer_impact_score=float(out.get("customer_impact_score", 0.0)),
                confidence=float(out.get("confidence", 0.7)),
                method="llm",
            )
        except Exception:
            return None
        # Second-pass verification when low confidence (mirrors the reference).
        if intent.confidence < 0.85:
            self.log_verify = True
        return intent

    # ---- deterministic fallback -----------------------------------------
    def _classify_rules(self, text: str, e) -> Intent:
        self.fallback()
        primary, conf = self.domain.classify_keywords(text)
        urgency = _SEVERITY_URGENCY.get(e.severity.upper(), Urgency.P4)
        return Intent(
            primary_intent=primary,
            urgency_tier=urgency,
            domains=self.domain.DOMAIN_TAGS.get(primary, []),
            customer_impact_score=0.0,
            confidence=conf,
            method="keyword_fallback",
        )
