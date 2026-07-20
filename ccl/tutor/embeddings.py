"""Embeddings for semantic retrieval (section 8, retrieval swap).

This is the seam for vector retrieval. Two kinds of embedder share one
interface, mirroring the provider adapter pattern (live model vs offline stub):

* ``LocalHashingEmbedder`` — deterministic, dependency-free, offline. It maps
  text into a fixed-dimension vector by feature-hashing its tokens, then L2
  normalizes. Cosine similarity between two such vectors rises with shared
  vocabulary. This makes the *entire* vector-retrieval path real and testable
  without any external service — but note it does NOT capture true semantics
  (synonyms, paraphrase). "arrows tip-to-tail" and "vector addition" share no
  tokens, so it will not match them. That requires a learned model, which is
  the deferred infrastructure piece (an embeddings provider + a vector store).

* A provider-backed embedder (e.g. a hosted embeddings API) drops in here when
  that infrastructure is configured. The retriever, the approved-source filter,
  and the ``embed``/``embed_one`` interface are unchanged when it does, so no
  policy or verifier rules move.

Selection is by the ``CCL_EMBED`` environment variable (default ``local``).
"""

from __future__ import annotations

import hashlib
import math
import os
import re
from typing import Protocol

_WORD = re.compile(r"[a-z0-9]+")


def _tokens(text: str) -> list[str]:
    # Matches KeywordRetriever's tokenization so the two retrievers see the same
    # surface terms; only the scoring differs.
    return [w for w in _WORD.findall(text.lower()) if len(w) > 2]


class Embedder(Protocol):
    """Turns text into fixed-dimension, L2-normalized vectors."""

    dim: int

    def embed(self, texts: list[str]) -> list[list[float]]: ...

    def embed_one(self, text: str) -> list[float]: ...


class LocalHashingEmbedder:
    """Deterministic offline embedder via feature hashing.

    Each token is hashed (stable MD5, not Python's salted ``hash``) to a bucket
    and a sign; counts accumulate into a ``dim``-length vector that is then L2
    normalized. Deterministic across processes, so tests and demos are stable.
    """

    def __init__(self, dim: int = 256) -> None:
        self.dim = dim

    def _hash(self, token: str) -> tuple[int, float]:
        h = int(hashlib.md5(token.encode("utf-8")).hexdigest(), 16)
        bucket = h % self.dim
        sign = 1.0 if (h >> 8) & 1 else -1.0
        return bucket, sign

    def embed_one(self, text: str) -> list[float]:
        vec = [0.0] * self.dim
        for tok in _tokens(text):
            bucket, sign = self._hash(tok)
            vec[bucket] += sign
        norm = math.sqrt(sum(v * v for v in vec))
        if norm == 0.0:
            return vec  # all-zero: no usable tokens
        return [v / norm for v in vec]

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [self.embed_one(t) for t in texts]


def cosine(a: list[float], b: list[float]) -> float:
    """Cosine similarity of two equal-length vectors (0.0 if either is zero)."""
    if len(a) != len(b):
        raise ValueError("vector length mismatch")
    return sum(x * y for x, y in zip(a, b))  # inputs are already L2-normalized


def make_embedder(name: str | None = None) -> Embedder:
    """Select an embedder. Defaults to the offline local embedder.

    To add a hosted provider: implement an ``Embedder`` that calls the service,
    then return it here for its name (and gate on the presence of an API key).
    """
    name = (name or os.environ.get("CCL_EMBED", "local")).lower()
    if name == "local":
        return LocalHashingEmbedder()
    raise ValueError(
        f"unknown embedder {name!r}; supported: 'local' "
        "(add a provider-backed Embedder in ccl/tutor/embeddings.py to enable more)"
    )
