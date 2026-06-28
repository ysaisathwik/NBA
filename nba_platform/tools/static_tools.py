"""Static fast-path tools.

These are deterministic, LLM-free lookups that agents call *before* any LLM reasoning. If a
tool returns a useful result the agent can skip or shrink its LLM call; if it returns ``None``
the agent proceeds to full reasoning. This is the concrete form of the reference's
"Tool-or-LLM fallback" discipline.

The functions are pure (depend only on the store / domain passed in) and are wired into the
registry by :func:`nba_platform.tools.catalog.build_registry`.
"""
from __future__ import annotations

from typing import Any


def keyword_classifier(domain, text: str) -> dict[str, Any]:
    """Regex/keyword intent classifier — the fallback when the LLM is unavailable."""
    intent, confidence = domain.classify_keywords(text or "")
    return {"intent": intent, "confidence": confidence}


def asset_health_check(store, asset_id: str | None) -> dict[str, Any] | None:
    """Quick asset health_score lookup (no LLM)."""
    if not asset_id:
        return None
    asset = store.get_asset(asset_id)
    if not asset:
        return None
    return {"asset_id": asset_id, "health_score": asset.get("health_score"),
            "last_maintenance": asset.get("last_maintenance"), "type": asset.get("type")}


def sla_lookup(store, customer_id: str | None) -> dict[str, Any] | None:
    """Deterministic customer SLA tier lookup."""
    if not customer_id:
        return None
    cust = store.get_customer(customer_id)
    if not cust:
        return None
    return {"customer_id": customer_id, "tier": cust.get("tier"), "contract": cust.get("contract")}


def action_template_fetch(domain, intent: str, severity: str | None = None) -> list[dict[str, Any]] | None:
    """Fetch rule-based action templates by intent (× severity) — the static fast path
    the Recommendation Agent uses before / instead of generative reasoning."""
    templates = domain.action_templates(intent)
    return templates or None


def knowledge_summary_fetch(store, query: str, min_similarity: float = 0.45) -> dict[str, Any] | None:
    """Return a cached knowledge summary if one is a strong match; else None (→ full search)."""
    rows = store.vector_search(query or "", top_k=1, content_type="knowledge")
    if rows and rows[0].get("similarity", 0.0) >= min_similarity:
        top = rows[0]
        return {"summary": top.get("chunk_text"), "source": top.get("source_id"),
                "similarity": top.get("similarity")}
    return None


def sentiment_baseline(store, customer_id: str | None) -> dict[str, Any] | None:
    """Customer baseline sentiment (the pre-event reference point for verification)."""
    if not customer_id:
        return None
    cust = store.get_customer(customer_id)
    if not cust:
        return None
    return {"customer_id": customer_id, "sentiment": cust.get("sentiment")}
