"""Clustering of feedback embeddings into candidate problem themes.

- ``agglomerative`` (average linkage, cosine distance, distance threshold):
  no need to choose k; good for up to a few thousand records. O(n²) memory.
- ``hdbscan``: density-based, natively labels noise; scales better.
- ``auto``: agglomerative up to 6,000 records (≈3.6 s, ~420 MB peak measured), HDBSCAN beyond.
  On the bundled data HDBSCAN over-fragments themes (docs/AI_EVALUATION.md), so it is a fallback for scale.

In every mode, clusters smaller than ``min_cluster_size`` become *unclustered*
(label -1) instead of being forced into a theme.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

AUTO_SWITCH_N = 6000


@dataclass
class ClusteringResult:
    labels: np.ndarray  # -1 = unclustered
    algorithm: str
    note: str = ""

    @property
    def n_clusters(self) -> int:
        return len({int(x) for x in self.labels if x >= 0})


def cluster_vectors(
    vectors: np.ndarray,
    algorithm: str = "auto",
    distance_threshold: float = 0.6,
    min_cluster_size: int = 3,
) -> ClusteringResult:
    n = len(vectors)
    min_cluster_size = max(2, int(min_cluster_size))
    labels = np.full(n, -1)
    # Zero vectors (e.g. text made only of stop words under TF-IDF) carry no signal: never cluster them.
    usable = np.where(np.linalg.norm(vectors, axis=1) > 1e-6)[0] if n else np.array([], dtype=int)
    if len(usable) < max(2 * min_cluster_size, 6):
        return ClusteringResult(
            labels,
            "none",
            f"Only {len(usable)} analysable record(s); at least {max(2 * min_cluster_size, 6)} "
            "are needed to form themes.",
        )
    if algorithm == "auto":
        algorithm = "agglomerative" if len(usable) <= AUTO_SWITCH_N else "hdbscan"
    x = vectors[usable]

    if algorithm == "agglomerative":
        from sklearn.cluster import AgglomerativeClustering

        model = AgglomerativeClustering(
            n_clusters=None, metric="cosine", linkage="average", distance_threshold=distance_threshold
        )
        raw = model.fit_predict(x)
    elif algorithm == "hdbscan":
        from sklearn.cluster import HDBSCAN

        # Density clustering fragments large datasets into many micro-themes when the minimum size is
        # small, so it scales with data size (1% of records) — measured in docs/AI_EVALUATION.md.
        hdb_min = max(min_cluster_size, round(0.01 * len(x)))
        # On unit vectors, euclidean distance is a monotone function of cosine distance.
        raw = HDBSCAN(min_cluster_size=hdb_min, min_samples=max(2, hdb_min // 2)).fit_predict(x)
    else:
        raise ValueError(f"Unknown clustering algorithm '{algorithm}'.")

    sizes: dict[int, int] = {}
    for lab in raw:
        if lab >= 0:
            sizes[int(lab)] = sizes.get(int(lab), 0) + 1
    # Relabel kept clusters 0..k-1 by descending size for stable display order.
    kept = sorted((lab for lab, s in sizes.items() if s >= min_cluster_size), key=lambda x: (-sizes[x], x))
    remap = {lab: i for i, lab in enumerate(kept)}
    for pos, lab in zip(usable, raw, strict=True):
        if int(lab) in remap:
            labels[pos] = remap[int(lab)]
    hint = "a larger distance threshold" if algorithm == "agglomerative" else "a smaller minimum theme size"
    note = "" if kept else f"No group reached the minimum theme size; try {hint}."
    return ClusteringResult(labels, algorithm, note)


def centroid_similarities(vectors: np.ndarray, labels: np.ndarray) -> tuple[np.ndarray, dict[int, float]]:
    """Cosine similarity of each member to its cluster centroid, and mean per cluster (cohesion)."""
    sims = np.zeros(len(vectors), dtype=np.float32)
    cohesion: dict[int, float] = {}
    for lab in sorted({int(x) for x in labels if x >= 0}):
        idx = np.where(labels == lab)[0]
        centroid = vectors[idx].mean(axis=0)
        norm = np.linalg.norm(centroid) or 1.0
        s = vectors[idx] @ (centroid / norm)
        sims[idx] = s
        cohesion[lab] = float(s.mean())
    return sims, cohesion
