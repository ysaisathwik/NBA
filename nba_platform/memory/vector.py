"""Semantic (vector) tier helpers.

The vector store physically lives inside :class:`~nba_platform.memory.longterm.LongTermStore`
(the ``embeddings`` table + Python cosine search), exactly as pgvector lives inside Supabase
in the reference architecture. This module exposes a thin, intention-revealing façade so
callers can depend on "the semantic tier" rather than table mechanics.
"""
from __future__ import annotations

from typing import Any

from .longterm import LongTermStore


class SemanticIndex:
    def __init__(self, store: LongTermStore) -> None:
        self.store = store

    def search(self, query: str, top_k: int = 12, content_type: str | None = None) -> list[dict[str, Any]]:
        return self.store.vector_search(query, top_k=top_k, content_type=content_type)

    def index(self, content_type: str, source_id: str, text: str, metadata: dict | None = None) -> str:
        return self.store.add_chunk(content_type, source_id, text, metadata)
