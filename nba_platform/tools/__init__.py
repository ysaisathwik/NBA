"""Reusable tool architecture.

Tools are the deterministic capability layer agents reach for *before* falling back to an
LLM (the reference's "Tool-or-LLM fallback"). Each tool has a stable name + typed ``run``;
agents resolve them by name from the :class:`ToolRegistry`, which makes both agents and tools
independently testable and swappable.
"""
from .base import Tool, ToolCall, ToolRegistry
from .catalog import build_registry

__all__ = ["Tool", "ToolCall", "ToolRegistry", "build_registry"]
