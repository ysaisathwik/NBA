"""Risk Assessment Agent — multi-dimensional, deterministic risk scoring per action."""
from __future__ import annotations

from typing import Any

from .base import Agent
from ..schemas import RiskVector


class RiskAgent(Agent):
    name = "risk"
    critical = True

    def _run(self, session) -> dict[str, Any]:
        e = session.event
        ctx = session.mem.get_context()
        intent = session.mem.get_state("intent", {}) or {}
        candidates = session.mem.get_candidates()

        asset = ctx.get("asset_record") or {}
        context = {
            "customer_impact_score": intent.get("customer_impact_score", 0.0),
            "severity": e.severity,
            "hv": "HV" in str(asset.get("metadata", "")),
        }

        per_action: dict[str, dict[str, float]] = {}
        vectors: list[RiskVector] = []
        rules_ok = True
        for c in candidates:
            try:
                dims = self.domain.risk_for(c["action_type"], context)
            except Exception:
                # rules engine unavailable → conservative default (force human review)
                rules_ok = False
                dims = {k: 0.9 for k in ("financial", "safety", "compliance", "reputational", "operational")}
            per_action[c["action_type"]] = dims
            rv = RiskVector(**dims)
            vectors.append(rv)
            c["risk"] = dims  # attach back onto the candidate

        # aggregate = the risk of the top-ranked candidate (what we'd execute)
        top_vec = vectors[0] if vectors else RiskVector()
        aggregate_level = top_vec.level().value

        risk_blob = {
            "per_action": per_action,
            "aggregate": {
                "financial": top_vec.financial, "safety": top_vec.safety,
                "compliance": top_vec.compliance, "reputational": top_vec.reputational,
                "operational": top_vec.operational, "level": aggregate_level,
            },
            "rules_engine_ok": rules_ok,
        }
        session.mem.set_blob("risk", risk_blob)
        session.mem.set_candidates(candidates)  # persist attached risk

        self._confidence = 0.9 if rules_ok else 0.5
        if not rules_ok:
            session.mem.set_state("force_human_review", True)
        return {"aggregate_level": aggregate_level, "scored": len(candidates)}
