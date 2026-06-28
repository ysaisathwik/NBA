"""Wire concrete tools to the long-term store + simulators and build a registry."""
from __future__ import annotations

from ..memory.longterm import LongTermStore
from . import static_tools as st
from .base import Tool, ToolRegistry
from .simulated import Simulators


def build_registry(store: LongTermStore, sims: Simulators, domain=None) -> ToolRegistry:
    reg = ToolRegistry()

    def add(name: str, ttype: str, desc: str, fn) -> None:
        reg.register(Tool(name, ttype, desc, fn))

    # ---- retrieval (vector + structured) --------------------------------
    add("vector_search", "vector", "Semantic search over the embeddings store",
        lambda query, top_k=12, content_type=None: store.vector_search(query, top_k, content_type))
    add("sop_search", "vector", "Semantic search over SOPs / manuals / regulatory docs",
        lambda query, top_k=6: store.vector_search(query, top_k, content_type="knowledge"))
    add("episodic_lookup", "supabase", "Find past cases by fingerprint + semantic similarity",
        lambda fingerprint, query, top_k=4: store.find_episodic(fingerprint, query, top_k))
    add("asset_fetch", "supabase", "Fetch full asset record",
        lambda asset_id: store.get_asset(asset_id))
    add("customer_fetch", "supabase", "Fetch customer profile + tier",
        lambda customer_id: store.get_customer(customer_id) if customer_id else None)
    add("open_tickets", "supabase", "List open tickets for an asset, severity desc",
        lambda asset_id: store.open_tickets(asset_id) if asset_id else [])

    # ---- risk / cost / compliance ---------------------------------------
    add("erp_cost", "rest", "Estimate financial cost of an action",
        lambda action_type: sims.erp.cost_estimate(action_type))
    add("resource_availability", "rest", "Check crew/parts availability for feasibility",
        lambda action_type: sims.erp.resource_availability(action_type))

    # ---- telemetry / anomaly --------------------------------------------
    add("scada_telemetry", "rest", "Read current sensor value for an asset",
        lambda sim: sims.scada.read(sim))
    add("telemetry_stats", "rest", "90-day baseline stats for z-score",
        lambda sim: sims.scada.history_stats(sim))
    add("weather", "rest", "Current weather at asset location",
        lambda lat=None, lon=None: sims.weather.current(lat, lon))

    # ---- execution ------------------------------------------------------
    add("create_work_order", "rest", "Create + assign an ERP work order",
        lambda action_type, asset_id=None: sims.erp.create_work_order(action_type, asset_id))
    add("dispatch_crew", "rest", "Dispatch nearest qualified crew",
        lambda work_order_id: sims.fieldops.dispatch(work_order_id))
    add("crm_update", "rest", "Update CRM ticket status",
        lambda ticket_id, status: sims.crm.update_ticket(ticket_id, status))
    add("notify", "rest", "Send customer / stakeholder notifications",
        lambda audience, count=1, channel="email", subject="", body="": sims.comms.notify(audience, count))
    add("automation_trigger", "rest", "Trigger a pre-built automation workflow",
        lambda workflow: sims.automation.trigger(workflow))
    add("asset_update", "supabase", "Update asset health record",
        lambda asset_id, fields: _update_asset(store, asset_id, fields))

    # ---- verification ---------------------------------------------------
    add("crm_sentiment", "rest", "Latest customer sentiment score",
        lambda base, elapsed_min: sims.crm.sentiment(base, elapsed_min))
    add("customer_survey", "rest", "Send post-resolution micro-survey",
        lambda customer_id=None: sims.comms.survey(customer_id))

    # ---- audit ----------------------------------------------------------
    add("audit_write", "supabase", "Append an entry to the audit log",
        lambda user_id, action, entity_type, entity_id, payload: store.audit(
            user_id, action, entity_type, entity_id, payload))

    # ---- static fast-path tools (LLM-free) ------------------------------
    add("keyword_classifier", "internal", "Regex/keyword intent fallback (DialogueParser)",
        lambda text: st.keyword_classifier(domain, text))
    add("asset_health_check", "supabase", "Quick asset health_score lookup (AnomalyAgent)",
        lambda asset_id: st.asset_health_check(store, asset_id))
    add("sla_lookup", "supabase", "Customer SLA tier lookup (RiskAgent)",
        lambda customer_id: st.sla_lookup(store, customer_id))
    add("action_template_fetch", "supabase", "Rule-based action templates by intent×severity (RecommendationAgent)",
        lambda intent, severity=None: st.action_template_fetch(domain, intent, severity))
    add("knowledge_summary_fetch", "vector", "Cached knowledge summary fast path (KnowledgeAgent)",
        lambda query: st.knowledge_summary_fetch(store, query))
    add("sentiment_baseline", "supabase", "Customer baseline sentiment (VerificationAgent)",
        lambda customer_id: st.sentiment_baseline(store, customer_id))

    return reg


def _update_asset(store: LongTermStore, asset_id: str, fields: dict) -> dict:
    asset = store.get_asset(asset_id)
    if not asset:
        return {"updated": False}
    asset.update(fields)
    store.upsert("assets", asset)
    return {"updated": True, "asset_id": asset_id}
