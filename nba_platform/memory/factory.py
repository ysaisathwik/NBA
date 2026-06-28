"""Pick the long-term store backend: Supabase when configured, else SQLite."""
from __future__ import annotations

from ..config import Settings
from .longterm import LongTermStore


def build_store(settings: Settings):
    """Return a long-term store. Falls back to SQLite if Supabase is unavailable."""
    if settings.supabase_configured:
        try:
            from .supabase_store import SupabaseStore

            store = SupabaseStore(settings.supabase_url, settings.supabase_key)
            print("[NBA] Long-term store: Supabase (Postgres)")
            return store
        except Exception as exc:  # missing package, bad creds, network — degrade gracefully
            print(f"[NBA] Supabase unavailable ({type(exc).__name__}: {exc}); falling back to SQLite")
    return LongTermStore(settings.db_path)
