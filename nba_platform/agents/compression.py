"""Memory Compression Agent — summarise the case, embed it, store episodically, flush Redis."""
from __future__ import annotations

from typing import Any

from .base import Agent
from ..schemas import new_id, utcnow


class MemoryCompressionAgent(Agent):
    name = "compression"

    def _run(self, session) -> dict[str, Any]:
        e = session.event
        ctx = session.mem.get_context()
        candidates = session.mem.get_candidates()
        review = session.mem.get_blob("human_review", {}) or {}
        verify = session.mem.get_blob("verify", {}) or {}
        exec_result = session.mem.get_blob("exec_result", {}) or {}
        intent = session.mem.get_state("intent", {}) or {}

        recommended = candidates[0] if candidates else {}
        executed = next((c for c in candidates if c["id"] == review.get("selected_action")),
                        recommended)
        outcome = "success" if verify.get("resolved") else ("partial" if verify.get("partial_resolve") else "failure")

        summary = self._summary_llm(session, executed, outcome) or self._summary_template(
            e, ctx, executed, outcome, exec_result, verify)

        fingerprint = e.fingerprint()
        case_id = f"case-{utcnow()[:10]}-{session.sid[-6:]}"
        self.store.add_episodic({
            "id": case_id,
            "event_fingerprint": fingerprint,
            "context_summary": summary,
            "actions_taken": [{"action_type": executed.get("action_type"), "outcome": outcome}],
            "outcome": outcome,
            "confidence_delta": 0.0,
        })

        # Chunk the summary (smaller chunks → higher retrieval precision) into the vector tier.
        chunks = self._chunk(summary, size=240)
        for i, ch in enumerate(chunks):
            self.store.add_chunk("episodic_summary", case_id, ch, {"case": case_id, "chunk_index": i})

        self.store.save_conversation(session.sid, review.get("reviewer_id", "system"), session.trace,
                                     resolved_at=utcnow() if outcome == "success" else None)

        flushed = session.mem.flush()
        # Re-create the minimal state the Learning Agent needs (Redis was just flushed).
        session.mem.set_state("case_id", case_id)
        session.mem.set_blob("learning_input", {
            "case_id": case_id,
            "pattern_key": f"{intent.get('primary_intent','')}|{(ctx.get('asset_record') or {}).get('type','')}|{e.severity}",
            "recommended_action": recommended.get("action_type"),
            "executed_action": executed.get("action_type"),
            "modified": review.get("decision") == "modified",
            "outcome": outcome,
        })
        self._confidence = 0.9
        return {"case_id": case_id, "outcome": outcome, "chunks": len(chunks), "redis_keys_flushed": flushed}

    def _summary_template(self, e, ctx, executed, outcome, exec_result, verify) -> str:
        asset = (ctx.get("asset_record") or {}).get("name", e.asset_id or "asset")
        p1 = (f"Event: {e.type} on {asset} (severity {e.severity}"
              + (f", {e.metric}={e.value} vs threshold {e.threshold}" if e.value is not None else "") + "). "
              f"Context: {len(ctx.get('semantic_chunks') or [])} knowledge chunks, "
              f"{len(ctx.get('episodic_matches') or [])} episodic matches, "
              f"open tickets {[t['id'] for t in ctx.get('open_tickets') or []]}.")
        p2 = (f"Action taken: {executed.get('action_type')} — {executed.get('description','')}. "
              f"Execution {'succeeded' if exec_result.get('success') else 'failed'} with "
              f"{len(exec_result.get('operations', []))} operations (WO {exec_result.get('work_order_id')}).")
        p3 = (f"Outcome: {outcome}. Verification evidence: "
              f"telemetry normalised={verify.get('evidence',{}).get('telemetry',{}).get('normalised')}, "
              f"customer ok={verify.get('evidence',{}).get('customer',{}).get('ok')}. "
              f"Lesson: {executed.get('action_type')} is effective for this signature.")
        return "\n".join([p1, p2, p3])

    def _summary_llm(self, session, executed, outcome) -> str | None:
        if not self.llm.available:
            return None
        self._mode = "llm"
        out = self.llm.complete(
            system="Summarise the resolved case in exactly 3 paragraphs: (1) event+context, (2) actions+rationale, (3) outcome+lessons.",
            user=f"Trace: {session.trace[-12:]}\nExecuted: {executed}\nOutcome: {outcome}",
            fast=True,
        )
        return out.strip() if out else None

    @staticmethod
    def _chunk(text: str, size: int = 240) -> list[str]:
        words = text.split()
        chunks, cur, length = [], [], 0
        for w in words:
            cur.append(w)
            length += len(w) + 1
            if length >= size:
                chunks.append(" ".join(cur))
                cur, length = [], 0
        if cur:
            chunks.append(" ".join(cur))
        return chunks or [text]
