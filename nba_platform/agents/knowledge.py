"""Knowledge Agent — retrieves SOPs, manuals, regulatory clauses (procedural knowledge)."""
from __future__ import annotations

from typing import Any

from .base import Agent


class KnowledgeAgent(Agent):
    name = "knowledge"

    def _run(self, session) -> dict[str, Any]:
        e = session.event
        ctx = session.mem.get_context()
        intent = session.mem.get_state("intent", {})
        asset = ctx.get("asset_record") or {}

        query = " ".join(str(x) for x in [
            intent.get("primary_intent"), asset.get("type"), e.metric, "procedure SOP standard"
        ] if x)
        chunks = self.use("sop_search", query=query, top_k=6) or []

        # Cold start → broaden query from specific asset to asset type (EC-02).
        if ctx.get("cold_start") and len(chunks) < 3:
            chunks = self.use("sop_search", query=f"{asset.get('type','')} general procedure", top_k=6) or []

        knowledge_chunks = [
            {"id": c.get("id"), "doc": c.get("metadata", {}).get("doc", c.get("source_id")),
             "text": c.get("chunk_text"), "similarity": c.get("similarity")}
            for c in chunks
        ]
        session.mem.update_context(knowledge_chunks=knowledge_chunks)

        low_coverage = len(knowledge_chunks) < 3
        if low_coverage:
            session.mem.set_state("knowledge_low_coverage", True)

        # Optional 3-sentence synthesis (LLM if available).
        summary = self.llm.complete(
            system="Summarise energy-ops procedural knowledge in 3 sentences.",
            user="Knowledge excerpts:\n" + "\n".join(c["text"] or "" for c in knowledge_chunks[:4]),
            fast=True,
        )
        if summary:
            self._mode = "llm"
            session.mem.update_context(knowledge_summary=summary.strip())

        self._confidence = 0.85 if not low_coverage else 0.55
        return {"knowledge_chunks": [c["doc"] for c in knowledge_chunks], "low_coverage": low_coverage}
