"""Context Retrieval Agent — assembles the full context package for the event."""
from __future__ import annotations

from typing import Any

from .base import Agent


class ContextAgent(Agent):
    name = "context"
    critical = True

    def _run(self, session) -> dict[str, Any]:
        e = session.event
        settings = self.platform.settings

        asset = self.use("asset_fetch", asset_id=e.asset_id) if e.asset_id else None
        customer = self.use("customer_fetch", customer_id=e.customer_id) if e.customer_id else None
        tickets = self.use("open_tickets", asset_id=e.asset_id) if e.asset_id else []

        query = e.raw_content or " ".join(
            str(x) for x in [e.type, e.metric, (asset or {}).get("name"), (asset or {}).get("type")] if x
        )
        fingerprint = e.fingerprint()
        episodic = self.use("episodic_lookup", fingerprint=fingerprint, query=query, top_k=4) or []
        chunks = self.use("vector_search", query=query, top_k=12) or []

        # LLM re-rank when retrieval is noisy (>20 chunks). Here it is a no-op hook.
        if len(chunks) > 20:
            self._mode = "llm"

        top_sim = max((c.get("similarity", 0.0) for c in chunks), default=0.0)
        # Cold start = weak semantic retrieval AND no strong episodic precedent (EC-02).
        strong_episodic = any(m.get("similarity", 0.0) >= 0.70 for m in episodic)
        cold_start = top_sim < settings.similarity_threshold and not strong_episodic
        if cold_start:
            session.mem.set_state("cold_start", True)

        context = {
            "asset_record": asset,
            "customer_profile": customer,
            "open_tickets": tickets,
            "episodic_matches": episodic,
            "semantic_chunks": chunks,
            "cold_start": cold_start,
            "top_similarity": round(top_sim, 3),
            "fingerprint": fingerprint,
            "serves_customers": (asset or {}).get("serves_customers"),
        }
        session.mem.set_context(context)

        self._confidence = 0.6 if cold_start else 0.9
        return {
            "asset": (asset or {}).get("id"),
            "open_tickets": [t["id"] for t in tickets],
            "episodic_matches": [m["id"] for m in episodic],
            "chunks": len(chunks),
            "top_similarity": round(top_sim, 3),
            "cold_start": cold_start,
        }
