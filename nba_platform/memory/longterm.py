"""Long-term store — Supabase/pgvector analogue backed by SQLite.

Holds the permanent tiers from the reference schema: structured records (assets, customers,
tickets), episodic memory, the vector store (embeddings), learning patterns, audit log, and
conversations. Vector similarity is computed in Python so there is zero infra to stand up.
"""
from __future__ import annotations

import json
import sqlite3
import threading
from typing import Any

from ..embeddings import cosine, embed
from ..schemas import utcnow

_SCHEMA = """
CREATE TABLE IF NOT EXISTS assets (
    id TEXT PRIMARY KEY, name TEXT, type TEXT, location TEXT, scada_id TEXT,
    installation_date TEXT, last_maintenance TEXT, health_score REAL, serves_customers INTEGER,
    lat REAL, lon REAL, metadata TEXT
);
CREATE TABLE IF NOT EXISTS customers (
    id TEXT PRIMARY KEY, name TEXT, tier TEXT, contract TEXT, sentiment REAL, metadata TEXT
);
CREATE TABLE IF NOT EXISTS tickets (
    id TEXT PRIMARY KEY, asset_id TEXT, type TEXT, severity TEXT, status TEXT,
    created_at TEXT, resolved_at TEXT, resolution_summary TEXT, metadata TEXT
);
CREATE TABLE IF NOT EXISTS episodic_memory (
    id TEXT PRIMARY KEY, event_fingerprint TEXT, context_summary TEXT, actions_taken TEXT,
    outcome TEXT, confidence_delta REAL, embedding TEXT, created_at TEXT
);
CREATE TABLE IF NOT EXISTS embeddings (
    id TEXT PRIMARY KEY, content_type TEXT, source_id TEXT, chunk_text TEXT,
    embedding TEXT, metadata TEXT
);
CREATE TABLE IF NOT EXISTS learning_patterns (
    pattern_hash TEXT PRIMARY KEY, feature_vector TEXT, recommended_action TEXT,
    success_rate REAL, sample_count INTEGER
);
CREATE TABLE IF NOT EXISTS audit_log (
    id TEXT PRIMARY KEY, user_id TEXT, action TEXT, entity_type TEXT, entity_id TEXT,
    payload TEXT, timestamp TEXT
);
CREATE TABLE IF NOT EXISTS conversations (
    id TEXT PRIMARY KEY, session_id TEXT, user_id TEXT, messages TEXT,
    created_at TEXT, resolved_at TEXT
);
"""


class LongTermStore:
    def __init__(self, path: str = ":memory:") -> None:
        self.path = path
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    # ----- generic helpers ------------------------------------------------
    def _exec(self, sql: str, params: tuple = ()) -> sqlite3.Cursor:
        with self._lock:
            cur = self._conn.execute(sql, params)
            self._conn.commit()
            return cur

    def _one(self, sql: str, params: tuple = ()) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(sql, params).fetchone()
            return dict(row) if row else None

    def _all(self, sql: str, params: tuple = ()) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(r) for r in self._conn.execute(sql, params).fetchall()]

    # ----- structured records --------------------------------------------
    def upsert(self, table: str, row: dict[str, Any]) -> None:
        cols = list(row.keys())
        placeholders = ",".join("?" for _ in cols)
        updates = ",".join(f"{c}=excluded.{c}" for c in cols if c != "id" and c != "pattern_hash")
        key = "pattern_hash" if table == "learning_patterns" else "id"
        sql = (
            f"INSERT INTO {table} ({','.join(cols)}) VALUES ({placeholders}) "
            f"ON CONFLICT({key}) DO UPDATE SET {updates}"
            if updates
            else f"INSERT OR REPLACE INTO {table} ({','.join(cols)}) VALUES ({placeholders})"
        )
        self._exec(sql, tuple(row[c] for c in cols))

    def get_asset(self, asset_id: str) -> dict[str, Any] | None:
        return self._one("SELECT * FROM assets WHERE id=?", (asset_id,))

    def get_asset_by_scada(self, scada_id: str) -> dict[str, Any] | None:
        return self._one("SELECT * FROM assets WHERE scada_id=?", (scada_id,))

    def get_customer(self, customer_id: str) -> dict[str, Any] | None:
        return self._one("SELECT * FROM customers WHERE id=?", (customer_id,))

    def open_tickets(self, asset_id: str) -> list[dict[str, Any]]:
        return self._all(
            "SELECT * FROM tickets WHERE asset_id=? AND status!='RESOLVED' "
            "AND status!='CLOSED' ORDER BY severity DESC",
            (asset_id,),
        )

    def update_ticket(self, ticket_id: str, **fields: Any) -> None:
        existing = self._one("SELECT * FROM tickets WHERE id=?", (ticket_id,))
        if not existing:
            return
        existing.update(fields)
        self.upsert("tickets", existing)

    # ----- vector store ---------------------------------------------------
    def add_chunk(self, content_type: str, source_id: str, text: str, metadata: dict | None = None) -> str:
        cid = f"chunk-{abs(hash((source_id, text))) % 10**8}"
        self.upsert(
            "embeddings",
            {
                "id": cid,
                "content_type": content_type,
                "source_id": source_id,
                "chunk_text": text,
                "embedding": json.dumps(embed(text)),
                "metadata": json.dumps(metadata or {}),
            },
        )
        return cid

    def vector_search(
        self, query: str, top_k: int = 12, content_type: str | None = None
    ) -> list[dict[str, Any]]:
        qv = embed(query)
        rows = self._all(
            "SELECT * FROM embeddings WHERE (? IS NULL OR content_type=?)",
            (content_type, content_type),
        )
        scored = []
        for r in rows:
            try:
                sim = cosine(qv, json.loads(r["embedding"]))
            except Exception:
                sim = 0.0
            r["similarity"] = round(sim, 4)
            r["metadata"] = json.loads(r.get("metadata") or "{}")
            scored.append(r)
        scored.sort(key=lambda x: x["similarity"], reverse=True)
        return scored[:top_k]

    # ----- episodic memory ------------------------------------------------
    def find_episodic(self, fingerprint: str, query: str, top_k: int = 4) -> list[dict[str, Any]]:
        # exact fingerprint matches first, then semantic neighbours
        exact = self._all("SELECT * FROM episodic_memory WHERE event_fingerprint=?", (fingerprint,))
        qv = embed(query)
        rows = self._all("SELECT * FROM episodic_memory")
        scored = []
        seen = {e["id"] for e in exact}
        for r in rows:
            try:
                sim = cosine(qv, json.loads(r["embedding"])) if r.get("embedding") else 0.0
            except Exception:
                sim = 0.0
            r["similarity"] = round(sim, 4)
            scored.append(r)
        for e in exact:
            e["similarity"] = max(e.get("similarity", 0.0), 0.95)
        scored = [r for r in scored if r["id"] not in seen]
        scored.sort(key=lambda x: x["similarity"], reverse=True)
        result = exact + scored
        for r in result:
            if isinstance(r.get("actions_taken"), str):
                try:
                    r["actions_taken"] = json.loads(r["actions_taken"])
                except Exception:
                    pass
        return result[:top_k]

    def add_episodic(self, row: dict[str, Any]) -> None:
        summary = row.get("context_summary", "")
        row.setdefault("embedding", json.dumps(embed(summary)))
        if isinstance(row.get("actions_taken"), (list, dict)):
            row["actions_taken"] = json.dumps(row["actions_taken"])
        row.setdefault("created_at", utcnow())
        self.upsert("episodic_memory", row)

    # ----- learning patterns ---------------------------------------------
    def get_pattern(self, pattern_hash: str) -> dict[str, Any] | None:
        return self._one("SELECT * FROM learning_patterns WHERE pattern_hash=?", (pattern_hash,))

    def upsert_pattern(self, row: dict[str, Any]) -> None:
        if isinstance(row.get("feature_vector"), (list, dict)):
            row["feature_vector"] = json.dumps(row["feature_vector"])
        self.upsert("learning_patterns", row)

    # ----- audit / conversations -----------------------------------------
    def audit(self, user_id: str, action: str, entity_type: str, entity_id: str, payload: dict) -> None:
        self.upsert(
            "audit_log",
            {
                "id": f"audit-{abs(hash((entity_id, action, utcnow()))) % 10**9}",
                "user_id": user_id,
                "action": action,
                "entity_type": entity_type,
                "entity_id": entity_id,
                "payload": json.dumps(payload, default=str),
                "timestamp": utcnow(),
            },
        )

    def audit_entries(self, limit: int = 50) -> list[dict[str, Any]]:
        return self._all("SELECT * FROM audit_log ORDER BY timestamp DESC LIMIT ?", (limit,))

    def save_conversation(self, session_id: str, user_id: str, messages: list, resolved_at: str | None) -> None:
        self.upsert(
            "conversations",
            {
                "id": f"conv-{session_id}",
                "session_id": session_id,
                "user_id": user_id,
                "messages": json.dumps(messages, default=str),
                "created_at": utcnow(),
                "resolved_at": resolved_at,
            },
        )

    def count(self, table: str) -> int:
        row = self._one(f"SELECT COUNT(*) AS c FROM {table}")
        return int(row["c"]) if row else 0

    def close(self) -> None:
        with self._lock:
            self._conn.close()
