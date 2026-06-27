"""Pluggable business-domain packs.

A domain pack supplies everything domain-specific: intent taxonomy, action templates, risk
rules, an auto-approve whitelist, planner fallback templates, and seed data. The agent
framework, memory, goal loop, and tools are domain-agnostic and transfer unchanged.
"""
from __future__ import annotations

import importlib
from typing import Any


def load_domain(name: str) -> Any:
    """Import a domain pack module by name (e.g. 'energy')."""
    return importlib.import_module(f"nba_platform.domain.{name}.config")


def seed_domain(name: str, store) -> None:
    mod = importlib.import_module(f"nba_platform.domain.{name}.seed")
    mod.seed(store)
