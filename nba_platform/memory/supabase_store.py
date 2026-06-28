"""Supabase/Postgres long-term store — drop-in for :class:`LongTermStore`.

Used when ``SUPABASE_URL`` + ``SUPABASE_KEY`` are configured and the ``supabase`` package is
installed. It implements the exact method surface the platform depends on. Vector similarity
is computed in Python over fetched rows (same cosine as the local embedder), so it works
against a plain Postgres table — no pgvector RPC required for the demo. Apply
``supabase_schema.sql`` to your project first.
"""
from __future__ import annotations

import json
from typing import Any

from ..embeddings import cosine, embed
from ..schemas import utcnow

_CONFLICT_KEY = {"learning_patterns": "pattern_hash"}


class SupabaseStore:
    def __init__(self, url: str, key: str) -> None:
        from supabase import create_client  # lazy import; optional dependency

        self.client = create_client(url, key)
        # connectivity probe — raises if the project/tables are unreachable
        self.client.table("assets").select("id").limit(1).execute()

    # ----- generic helpers ------------------------------------------------
    def _table(self, name: str):
        return self.client.table(name)

    def upsert(self, table: str, row: dict[str, Any]) -> None:
        self._table(table).upsert(row, on_conflict=_CONFLICT_KEY.get(table, "id")).execute()

    def _select_eq(self, table: str, **eq: Any) -> list[dict[str, Any]]:
        q = self._table(table).select("*")
        for k, v in eq.items():
            q = q.eq(k, v)
        return (q.execute().data) or []

    # ----- structured records --------------------------------------------
    def get_asset(self, asset_id: str) -> dict[str, Any] | None:
        rows = self._select_eq("assets", id=asset_id)
        return rows[0] if rows else None

    def get_asset_by_scada(self, scada_id: str) -> dict[str, Any] | None:
        rows = self._select_eq("assets", scada_id=scada_id)
        return rows[0] if rows else None

    def get_customer(self, customer_id: str) -> dict[str, Any] | None:
        rows = self._select_eq("customers", id=customer_id)
        return rows[0] if rows else None

    def open_tickets(self, asset_id: str) -> list[dict[str, Any]]:
        rows = self._select_eq("tickets", asset_id=asset_id)
        return [r for r in rows if r.get("status") not in {"RESOLVED", "CLOSED"}]

    def update_ticket(self, ticket_id: str, **fields: Any) -> None:
        self._table("tickets").update(fields).eq("id", ticket_id).execute()

    # ----- vector store ---------------------------------------------------
    def add_chunk(self, content_type: str, source_id: str, text: str, metadata: dict | None = None) -> str:
        cid = f"chunk-{abs(hash((source_id, text))) % 10**8}"
        self.upsert("embeddings", {
            "id": cid, "content_type": content_type, "source_id": source_id,
            "chunk_text": text, "embedding": json.dumps(embed(text)),
            "metadata": json.dumps(metadata or {}),
        })
        return cid

    def vector_search(self, query: str, top_k: int = 12, content_type: str | None = None) -> list[dict[str, Any]]:
        qv = embed(query)
        q = self._table("embeddings").select("*")
        if content_type:
            q = q.eq("content_type", content_type)
        rows = (q.execute().data) or []
        for r in rows:
            try:
                r["similarity"] = round(cosine(qv, json.loads(r["embedding"])), 4)
            except Exception:
                r["similarity"] = 0.0
            r["metadata"] = _loads(r.get("metadata"))
        rows.sort(key=lambda x: x["similarity"], reverse=True)
        return rows[:top_k]

    # ----- episodic memory ------------------------------------------------
    def find_episodic(self, fingerprint: str, query: str, top_k: int = 4) -> list[dict[str, Any]]:
        rows = (self._table("episodic_memory").select("*").execute().data) or []
        qv = embed(query)
        for r in rows:
            try:
                sim = cosine(qv, json.loads(r["embedding"])) if r.get("embedding") else 0.0
            except Exception:
                sim = 0.0
            if r.get("event_fingerprint") == fingerprint:
                sim = max(sim, 0.95)
            r["similarity"] = round(sim, 4)
            r["actions_taken"] = _loads(r.get("actions_taken"))
        rows.sort(key=lambda x: x["similarity"], reverse=True)
        return rows[:top_k]

    def add_episodic(self, row: dict[str, Any]) -> None:
        row.setdefault("embedding", json.dumps(embed(row.get("context_summary", ""))))
        if isinstance(row.get("actions_taken"), (list, dict)):
            row["actions_taken"] = json.dumps(row["actions_taken"])
        row.setdefault("created_at", utcnow())
        self.upsert("episodic_memory", row)

    # ----- learning patterns ---------------------------------------------
    def get_pattern(self, pattern_hash: str) -> dict[str, Any] | None:
        rows = self._select_eq("learning_patterns", pattern_hash=pattern_hash)
        return rows[0] if rows else None

    def upsert_pattern(self, row: dict[str, Any]) -> None:
        if isinstance(row.get("feature_vector"), (list, dict)):
            row["feature_vector"] = json.dumps(row["feature_vector"])
        self.upsert("learning_patterns", row)

    # ----- audit / conversations -----------------------------------------
    def audit(self, user_id: str, action: str, entity_type: str, entity_id: str, payload: dict) -> None:
        self.upsert("audit_log", {
            "id": f"audit-{abs(hash((entity_id, action, utcnow()))) % 10**9}",
            "user_id": user_id, "action": action, "entity_type": entity_type,
            "entity_id": entity_id, "payload": json.dumps(payload, default=str), "timestamp": utcnow(),
        })

    def audit_entries(self, limit: int = 50) -> list[dict[str, Any]]:
        return (self._table("audit_log").select("*").order("timestamp", desc=True).limit(limit).execute().data) or []

    def save_conversation(self, session_id: str, user_id: str, messages: list, resolved_at: str | None) -> None:
        self.upsert("conversations", {
            "id": f"conv-{session_id}", "session_id": session_id, "user_id": user_id,
            "messages": json.dumps(messages, default=str), "created_at": utcnow(), "resolved_at": resolved_at,
        })

    def count(self, table: str) -> int:
        try:
            res = self._table(table).select("*", count="exact").limit(1).execute()
            return int(res.count or 0)
        except Exception:
            return 0

    def close(self) -> None:  # nothing to close for the HTTP client
        pass


def _loads(value: Any) -> Any:
    if isinstance(value, str):
        try:
            return json.loads(value)
        except Exception:
            return value
    return value or {}
