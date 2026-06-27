"""Explainability Agent — evidence-backed rationale, citations, confidence, what-if."""
from __future__ import annotations

from typing import Any

from .base import Agent
from ..schemas import Explanation


class ExplainabilityAgent(Agent):
    name = "explainability"

    def _run(self, session) -> dict[str, Any]:
        ctx = session.mem.get_context()
        anomaly = session.mem.get_agent_output("anomaly", {}) or {}
        candidates = session.mem.get_candidates()
        risk_blob = session.mem.get_blob("risk", {}) or {}

        valid_chunk_ids = {c.get("id") for c in (ctx.get("semantic_chunks") or [])}
        valid_chunk_ids |= {c.get("id") for c in (ctx.get("knowledge_chunks") or [])}
        valid_chunk_ids |= {m.get("id") for m in (ctx.get("episodic_matches") or [])}

        low_coverage = bool(session.mem.get_state("knowledge_low_coverage"))
        explanations: list[Explanation] = []
        for c in candidates:
            exp = self._explain_one(c, ctx, anomaly, risk_blob, valid_chunk_ids, low_coverage, session)
            explanations.append(exp)

        out = [e.model_dump() for e in explanations]
        session.mem.set_blob("explanations", out)
        self._confidence = explanations[0].confidence if explanations else 0.0
        return {"explained": len(out), "top_confidence": self._confidence}

    def _explain_one(self, c, ctx, anomaly, risk_blob, valid_ids, low_coverage, session) -> Explanation:
        action = c["action_type"]
        # citations = evidence references that map to a real chunk id (citation validator)
        citations = [ev for ev in c.get("evidence", []) if any(cid and cid in ev for cid in valid_ids)]
        citations += [c["precedent_case"]] if c.get("precedent_case") else []

        retrieval_sim = ctx.get("top_similarity", 0.0)
        anomaly_conf = anomaly.get("confidence", 0.6)
        agg = risk_blob.get("aggregate", {})
        risk_certainty = 1.0 - 0.5 * (agg.get("safety", 0.0))  # higher safety risk → more uncertainty
        precedent = 0.91 if c.get("precedent_case") else 0.6

        confidence = round(
            0.30 * retrieval_sim + 0.20 * risk_certainty + 0.30 * anomaly_conf + 0.20 * precedent, 2)
        confidence = min(confidence, c.get("confidence", confidence))

        rationale = self._rationale_llm(c, ctx, anomaly) or self._rationale_template(c, anomaly, citations)

        risk_summary = (
            f"aggregate risk {agg.get('level','low')} (safety {agg.get('safety',0)}, "
            f"financial {agg.get('financial',0)}, compliance {agg.get('compliance',0)})")
        what_if = self._what_if(anomaly)

        disclaimer = None
        if low_coverage or ctx.get("cold_start"):
            disclaimer = "Limited knowledge-base coverage — confidence reduced; human review advised."

        return Explanation(
            action_id=c["id"], rationale=rationale, citations=citations,
            confidence=confidence, uncertainty=round(0.04 + 0.5 * agg.get("safety", 0.0) * 0.1, 2),
            risk_summary=risk_summary, what_if_not_acted=what_if,
            auto_generated=not self.llm.available, disclaimer=disclaimer,
        )

    def _rationale_template(self, c, anomaly, citations) -> str:
        bits = [f"Recommend '{c['action_type']}': {c['description']}"]
        if anomaly.get("detected"):
            bits.append(
                f"Telemetry shows {anomaly.get('anomaly_type')} (z={anomaly.get('z_score')}) sustained "
                f"{anomaly.get('duration_minutes')}min; predicted failure in "
                f"~{anomaly.get('predicted_failure_eta_minutes')}min if untreated.")
        if c.get("precedent_case"):
            bits.append(f"A near-identical past case ({c['precedent_case']}) was resolved this way.")
        if citations:
            bits.append("Evidence: " + ", ".join(citations[:3]) + ".")
        return " ".join(bits)

    def _rationale_llm(self, c, ctx, anomaly) -> str | None:
        if not self.llm.available:
            return None
        self._mode = "llm"
        chunks = "\n".join(f"[{k.get('id')}] {k.get('chunk_text') or k.get('text','')}"
                           for k in (ctx.get("knowledge_chunks") or [])[:4])
        out = self.llm.complete(
            system=("Write a 2-3 sentence, evidence-backed rationale. Append [chunk_id] inline for "
                    "every factual claim. Do not invent chunk ids."),
            user=f"Action: {c['action_type']} — {c['description']}\nAnomaly: {anomaly}\nChunks:\n{chunks}",
        )
        return out.strip() if out else None

    def _what_if(self, anomaly) -> str:
        if anomaly.get("predicted_failure_eta_minutes"):
            return (f"If no action is taken, predicted failure in ~{anomaly['predicted_failure_eta_minutes']} "
                    "minutes, risking outage and asset damage.")
        return "If no action is taken, the issue is likely to persist or escalate."
