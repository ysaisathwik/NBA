"""Recommendation Agent — the core intelligence: ranked, evidence-cited next best actions."""
from __future__ import annotations

from typing import Any

from .base import Agent
from ..schemas import Candidate


class RecommendationAgent(Agent):
    name = "recommendation"

    def _run(self, session) -> dict[str, Any]:
        e = session.event
        ctx = session.mem.get_context()
        intent = session.mem.get_state("intent", {}) or {}
        anomaly = session.mem.get_agent_output("anomaly", {}) or {}
        primary = intent.get("primary_intent", "routine_inquiry")

        templates = self.domain.action_templates(primary, anomaly.get("anomaly_type"))
        candidates: list[Candidate] = []
        for t in templates:
            candidates.append(Candidate(
                action_type=t["action_type"], description=t["description"],
                priority_score=t["base_confidence"], confidence=t["base_confidence"],
                estimated_impact=t["estimated_impact"], estimated_duration=t["estimated_duration"],
                prerequisites=list(t.get("prerequisites", [])),
                resource_requirements=list(t.get("resource_requirements", [])),
                template_derived=not self.llm.available,
            ))

        self._apply_episodic_precedent(ctx, candidates)
        self._apply_learning_bias(e, ctx, primary, candidates)
        self._apply_evidence(ctx, anomaly, candidates)
        self._apply_feasibility(candidates)
        self._apply_cold_start_ceiling(ctx, candidates)
        self._maybe_llm_enhance(session, candidates)

        # dedup by action_type, keep highest priority, then rank, keep top 5
        seen: dict[str, Candidate] = {}
        for c in candidates:
            if c.action_type not in seen or c.priority_score > seen[c.action_type].priority_score:
                seen[c.action_type] = c
        ranked = sorted(seen.values(), key=lambda c: c.priority_score, reverse=True)[:5]

        session.mem.set_candidates([c.model_dump() for c in ranked])
        self._confidence = ranked[0].confidence if ranked else 0.0
        return {"candidates": [(c.action_type, round(c.priority_score, 2)) for c in ranked]}

    # ---- enrichment steps -----------------------------------------------
    def _apply_episodic_precedent(self, ctx, candidates) -> None:
        matches = ctx.get("episodic_matches") or []
        if not matches:
            return
        top = matches[0]
        sim = top.get("similarity", 0.0)
        if sim < self.platform.settings.episodic_precedent_threshold:
            return
        actions = top.get("actions_taken") or []
        if not actions:
            return
        precedent_action = actions[0].get("action_type")
        for c in candidates:
            if c.action_type == precedent_action:
                c.priority_score = min(0.99, c.priority_score + 0.05)
                c.precedent_case = top.get("id")
                c.evidence.append(f"precedent {top.get('id')} resolved via {precedent_action} (sim {sim:.2f})")

    def _apply_learning_bias(self, e, ctx, primary, candidates) -> None:
        asset_type = (ctx.get("asset_record") or {}).get("type", "")
        pattern = self.store.get_pattern(f"{primary}|{asset_type}|{e.severity}")
        if not pattern:
            return
        action = pattern.get("recommended_action")
        rate = float(pattern.get("success_rate", 0.0))
        for c in candidates:
            if c.action_type == action:
                # nudge toward historically successful actions
                c.priority_score = min(0.99, c.priority_score + 0.03 * (rate - 0.5))
                c.evidence.append(f"learning: {action} success_rate={rate:.2f} over {pattern.get('sample_count')} cases")

    def _apply_evidence(self, ctx, anomaly, candidates) -> None:
        chunk_ids = [c.get("id") for c in (ctx.get("knowledge_chunks") or [])][:2]
        for c in candidates:
            if anomaly.get("detected"):
                c.evidence.append(
                    f"{anomaly.get('anomaly_type')} z={anomaly.get('z_score')} for "
                    f"{anomaly.get('duration_minutes')}min; root cause {anomaly.get('root_cause_hypothesis')}")
            c.evidence.extend(f"[{cid}]" for cid in chunk_ids)

    def _apply_feasibility(self, candidates) -> None:
        for c in candidates:
            avail = self.use("resource_availability", action_type=c.action_type) or {}
            c.feasible = bool(avail.get("available", True))
            if not c.feasible:
                c.priority_score *= 0.8

    def _apply_cold_start_ceiling(self, ctx, candidates) -> None:
        if ctx.get("cold_start"):
            for c in candidates:
                c.confidence = min(c.confidence, 0.70)
                c.priority_score = min(c.priority_score, 0.70)

    def _maybe_llm_enhance(self, session, candidates) -> None:
        if not self.llm.available:
            return
        self._mode = "llm"
        out = self.llm.complete(
            system="You are an energy-ops decision engine. Refine and rank next best actions.",
            user=(
                "Given these candidate actions (JSON-like), return JSON {ranking: [action_type...], "
                "notes: {action_type: one-line justification}} ordered best-first.\n"
                + "\n".join(f"- {c.action_type}: {c.description}" for c in candidates)
            ),
            json_mode=True,
        )
        if not isinstance(out, dict):
            return
        ranking = out.get("ranking") or []
        notes = out.get("notes") or {}
        order = {a: i for i, a in enumerate(ranking)}
        for c in candidates:
            if c.action_type in order:
                # blend model ordering into priority (small nudge to preserve determinism floor)
                c.priority_score = min(0.99, c.priority_score + 0.02 * (len(ranking) - order[c.action_type]))
            if c.action_type in notes:
                c.evidence.append(f"LLM: {notes[c.action_type]}")
