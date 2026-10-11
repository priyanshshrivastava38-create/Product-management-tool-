"""Exact and semantic near-duplicate detection.

Pairs are proposed for human review only — records are never merged.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class Pair:
    i: int
    j: int
    score: float
    kind: str  # exact | semantic


def find_similar_pairs(
    vectors: np.ndarray,
    hashes: list[str],
    threshold: float = 0.85,
    top_k: int = 5,
    max_pairs: int = 20000,
    chunk: int = 1024,
) -> list[Pair]:
    n = len(hashes)
    pairs: dict[tuple[int, int], Pair] = {}

    by_hash: dict[str, list[int]] = {}
    for idx, h in enumerate(hashes):
        by_hash.setdefault(h, []).append(idx)
    for members in by_hash.values():
        if len(members) > 1:
            first = members[0]
            for other in members[1:]:
                pairs[(first, other)] = Pair(first, other, 1.0, "exact")

    if n > 1 and vectors.shape[1] > 0:
        for start in range(0, n, chunk):
            block = vectors[start : start + chunk] @ vectors.T  # cosine (unit vectors)
            for r in range(block.shape[0]):
                i = start + r
                row = block[r]
                row[: i + 1] = -1  # upper triangle only, no self-pairs
                k = min(top_k, n - i - 1)
                if k <= 0:
                    continue
                cand = np.argpartition(-row, k - 1)[:k]
                for j in cand:
                    score = float(row[j])
                    if score >= threshold and (i, int(j)) not in pairs and hashes[i] != hashes[int(j)]:
                        pairs[(i, int(j))] = Pair(i, int(j), round(min(score, 1.0), 4), "semantic")

    ordered = sorted(pairs.values(), key=lambda p: (-p.score, p.i, p.j))
    return ordered[:max_pairs]
