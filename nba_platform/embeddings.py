"""Local, dependency-free embeddings + cosine similarity.

The reference stack uses OpenAI ``text-embedding-3-large`` + pgvector. To keep the platform
runnable anywhere (and offline), we ship a deterministic hashed bag-of-words embedder. It is
pluggable: swap :func:`embed` for a real provider and the rest of the platform is unchanged.
"""
from __future__ import annotations

import math
import re
from functools import lru_cache

from .config import get_settings

_TOKEN_RE = re.compile(r"[a-z0-9]+")
_STOP = {
    "the", "a", "an", "and", "or", "of", "to", "in", "on", "for", "is", "are", "was",
    "were", "be", "by", "with", "at", "as", "it", "this", "that", "from", "has", "have",
}


def tokenize(text: str) -> list[str]:
    return [t for t in _TOKEN_RE.findall((text or "").lower()) if t not in _STOP and len(t) > 1]


def _bucket(token: str, dim: int) -> int:
    # Stable hash independent of PYTHONHASHSEED.
    h = 0
    for ch in token:
        h = (h * 131 + ord(ch)) & 0xFFFFFFFF
    return h % dim


def embed(text: str, dim: int | None = None) -> list[float]:
    """Return an L2-normalised TF vector with sub-word smoothing for robustness."""
    dim = dim or get_settings().embedding_dim
    vec = [0.0] * dim
    tokens = tokenize(text)
    for tok in tokens:
        vec[_bucket(tok, dim)] += 1.0
        # add 3-gram smoothing so near-synonyms / typos still overlap a little
        for i in range(len(tok) - 2):
            vec[_bucket(tok[i : i + 3], dim)] += 0.3
    norm = math.sqrt(sum(v * v for v in vec))
    if norm == 0:
        return vec
    return [v / norm for v in vec]


def cosine(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    # vectors are already normalised by :func:`embed`; clamp for float noise
    return max(-1.0, min(1.0, dot))


@lru_cache(maxsize=4096)
def embed_cached(text: str) -> tuple[float, ...]:
    return tuple(embed(text))
