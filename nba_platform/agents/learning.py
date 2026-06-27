"""Learning Agent — updates pattern→outcome success rates after every resolved case."""
from __future__ import annotations

from typing import Any

from .base import Agent


class LearningAgent(Agent):
    name = "learning"
    critical = False  # fully async; never blocks resolution

    def _run(self, session) -> dict[str, Any]:
        li = session.mem.get_blob("learning_input", {}) or {}
        pattern_key = li.get("pattern_key")
        executed = li.get("executed_action")
        recommended = li.get("recommended_action")
        outcome = li.get("outcome", "failure")
        if not pattern_key or not executed:
            self._confidence = 0.0
            return {"updated": False, "reason": "insufficient_data"}

        # Outcome weight: success raises, partial mild, failure lowers.
        delta = {"success": 1.0, "partial": 0.6, "failure": 0.0}.get(outcome, 0.5)
        acceptance = recommended == executed  # did the human keep our top pick?

        existing = self.store.get_pattern(pattern_key)
        if existing:
            n = int(existing.get("sample_count", 0))
            old = float(existing.get("success_rate", 0.5))
            new_rate = round((old * n + delta) / (n + 1), 4)  # incremental online update
            self.store.upsert_pattern({
                "pattern_hash": pattern_key,
                "feature_vector": existing.get("feature_vector", "{}"),
                "recommended_action": executed if acceptance else existing.get("recommended_action", executed),
                "success_rate": new_rate, "sample_count": n + 1,
            })
            change = (old, new_rate)
        else:
            new_rate = delta
            self.store.upsert_pattern({
                "pattern_hash": pattern_key,
                "feature_vector": {"key": pattern_key},
                "recommended_action": executed, "success_rate": round(new_rate, 4), "sample_count": 1,
            })
            change = (None, new_rate)

        # A human modification is a high-signal example — flag systematic divergence for SOP review.
        flagged = li.get("modified") and not acceptance

        self.use("audit_write", user_id="learning_agent", action="pattern_update", entity_type="learning_pattern",
                 entity_id=pattern_key, payload={"outcome": outcome, "success_rate": new_rate, "accepted": acceptance})

        self._confidence = 0.9
        return {"updated": True, "pattern": pattern_key, "success_rate_change": change,
                "acceptance": acceptance, "flagged_for_sop_review": bool(flagged)}
