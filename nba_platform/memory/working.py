"""Working memory — a Redis-like, session-scoped agent blackboard with pub/sub.

Mirrors the reference Redis key design:
    session:{sid}:state      (hash)
    session:{sid}:agent:{n}  (hash)
    session:{sid}:context    (json)
    session:{sid}:candidates (list)
    session:{sid}:risk       (hash)
    session:{sid}:human_review / exec_result / verify (hash)
    pubsub:session:{sid}     (channel)

In-process implementation (dicts + callback fan-out). Swap for ``redis-py`` by keeping the
same method surface; namespacing and TTL semantics are preserved.
"""
from __future__ import annotations

import json
import threading
import time
from typing import Any, Callable

from ..schemas import utcnow


class WorkingMemory:
    def __init__(self, ttl_seconds: int = 24 * 3600) -> None:
        self._hash: dict[str, dict[str, Any]] = {}
        self._str: dict[str, str] = {}
        self._list: dict[str, list[Any]] = {}
        self._expiry: dict[str, float] = {}
        self._ttl = ttl_seconds
        self._subscribers: list[Callable[[dict[str, Any]], None]] = []
        self._lock = threading.RLock()

    # ----- expiry helpers -------------------------------------------------
    def _touch(self, key: str) -> None:
        self._expiry[key] = time.time() + self._ttl

    def _alive(self, key: str) -> bool:
        exp = self._expiry.get(key)
        return exp is None or exp > time.time()

    # ----- hash -----------------------------------------------------------
    def hset(self, key: str, field: str, value: Any) -> None:
        with self._lock:
            self._hash.setdefault(key, {})[field] = value
            self._touch(key)

    def hget(self, key: str, field: str, default: Any = None) -> Any:
        with self._lock:
            if not self._alive(key):
                return default
            return self._hash.get(key, {}).get(field, default)

    def hgetall(self, key: str) -> dict[str, Any]:
        with self._lock:
            if not self._alive(key):
                return {}
            return dict(self._hash.get(key, {}))

    # ----- string / json --------------------------------------------------
    def set(self, key: str, value: str) -> None:
        with self._lock:
            self._str[key] = value
            self._touch(key)

    def get(self, key: str, default: Any = None) -> Any:
        with self._lock:
            if not self._alive(key):
                return default
            return self._str.get(key, default)

    def set_json(self, key: str, value: Any) -> None:
        self.set(key, json.dumps(value, default=str))

    def get_json(self, key: str, default: Any = None) -> Any:
        raw = self.get(key)
        if raw is None:
            return default
        try:
            return json.loads(raw)
        except Exception:
            return default

    # ----- list -----------------------------------------------------------
    def rpush(self, key: str, value: Any) -> None:
        with self._lock:
            self._list.setdefault(key, []).append(value)
            self._touch(key)

    def lrange(self, key: str) -> list[Any]:
        with self._lock:
            if not self._alive(key):
                return []
            return list(self._list.get(key, []))

    # ----- key management -------------------------------------------------
    def keys(self, prefix: str) -> list[str]:
        with self._lock:
            allk = set(self._hash) | set(self._str) | set(self._list)
            return [k for k in allk if k.startswith(prefix) and self._alive(k)]

    def delete_prefix(self, prefix: str) -> int:
        """Redis SCAN + DEL — used by Memory Compression to flush a finished session."""
        with self._lock:
            removed = 0
            for store in (self._hash, self._str, self._list):
                for k in [k for k in store if k.startswith(prefix)]:
                    store.pop(k, None)
                    self._expiry.pop(k, None)
                    removed += 1
            return removed

    # ----- pub/sub --------------------------------------------------------
    def subscribe(self, callback: Callable[[dict[str, Any]], None]) -> None:
        self._subscribers.append(callback)

    def publish(self, channel: str, message: dict[str, Any]) -> None:
        msg = {"channel": channel, "ts": utcnow(), **message}
        for cb in list(self._subscribers):
            try:
                cb(msg)
            except Exception:
                pass


class SessionMemory:
    """Convenience wrapper that bakes the session_id into every key."""

    def __init__(self, wm: WorkingMemory, sid: str) -> None:
        self.wm = wm
        self.sid = sid

    def k(self, suffix: str) -> str:
        return f"session:{self.sid}:{suffix}"

    @property
    def channel(self) -> str:
        return f"pubsub:session:{self.sid}"

    # state hash
    def set_state(self, field: str, value: Any) -> None:
        self.wm.hset(self.k("state"), field, value)

    def get_state(self, field: str, default: Any = None) -> Any:
        return self.wm.hget(self.k("state"), field, default)

    def all_state(self) -> dict[str, Any]:
        return self.wm.hgetall(self.k("state"))

    # agent results
    def set_agent(self, name: str, result: dict[str, Any]) -> None:
        self.wm.set_json(self.k(f"agent:{name}"), result)

    def get_agent(self, name: str, default: Any = None) -> Any:
        return self.wm.get_json(self.k(f"agent:{name}"), default)

    def get_agent_output(self, name: str, default: Any = None) -> Any:
        """Return just the ``output`` payload from an agent's result wrapper."""
        wrapper = self.wm.get_json(self.k(f"agent:{name}"), None)
        if isinstance(wrapper, dict) and "output" in wrapper:
            return wrapper.get("output") or (default if default is not None else {})
        return default if default is not None else {}

    # context blob
    def set_context(self, ctx: dict[str, Any]) -> None:
        self.wm.set_json(self.k("context"), ctx)

    def get_context(self) -> dict[str, Any]:
        return self.wm.get_json(self.k("context"), {})

    def update_context(self, **kwargs: Any) -> None:
        ctx = self.get_context()
        ctx.update(kwargs)
        self.set_context(ctx)

    # candidates / risk / review / exec / verify
    def set_candidates(self, candidates: list[dict[str, Any]]) -> None:
        self.wm.set_json(self.k("candidates"), candidates)

    def get_candidates(self) -> list[dict[str, Any]]:
        return self.wm.get_json(self.k("candidates"), [])

    def set_blob(self, suffix: str, value: Any) -> None:
        self.wm.set_json(self.k(suffix), value)

    def get_blob(self, suffix: str, default: Any = None) -> Any:
        return self.wm.get_json(self.k(suffix), default)

    def publish(self, message: dict[str, Any]) -> None:
        self.wm.publish(self.channel, {"session_id": self.sid, **message})

    def flush(self) -> int:
        return self.wm.delete_prefix(f"session:{self.sid}:")
