"""Text embeddings with a local Sentence-Transformers model and a TF-IDF fallback.

- ``sentence-transformers``: semantic vectors from a small local model
  (default all-MiniLM-L6-v2, ~90 MB download on first use, ~300–500 MB RAM with torch).
  Vectors are cached in the database keyed by (text hash, model).
- ``tfidf``: TF-IDF (1–2 grams) reduced with truncated SVD (LSA). Lexical, not
  semantic — paraphrases with no shared words will not match — but it needs no
  model download and is fully deterministic.
- ``auto``: try sentence-transformers, fall back to tfidf with a recorded reason.

All vectors are L2-normalised so a dot product equals cosine similarity.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache

import numpy as np
from sqlalchemy import select
from sqlalchemy.orm import Session

from productpulse.db.models import EmbeddingCache
from productpulse.logging_setup import get_logger

log = get_logger("embeddings")


@dataclass
class EmbeddingResult:
    vectors: np.ndarray
    method: str  # e.g. "sentence-transformers:all-MiniLM-L6-v2" or "tfidf-lsa"
    fallback_reason: str | None = None
    cache_hits: int = 0

    @property
    def is_semantic(self) -> bool:
        return self.method.startswith("sentence-transformers")


class EmbeddingUnavailable(RuntimeError):
    pass


@lru_cache(maxsize=2)
def load_sentence_model(model_name: str):
    """Load once per process (Streamlit reruns reuse the cached instance)."""
    try:
        from sentence_transformers import SentenceTransformer
    except Exception as exc:  # ImportError, or torch failing to initialise
        raise EmbeddingUnavailable(f"sentence-transformers is not installed or failed to import: {exc}") from exc
    try:
        return SentenceTransformer(model_name, device="cpu")
    except Exception as exc:
        raise EmbeddingUnavailable(f"could not load model '{model_name}': {exc}") from exc


def _normalise(matrix: np.ndarray) -> np.ndarray:
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return (matrix / norms).astype(np.float32)


def tfidf_embed(texts: list[str]) -> np.ndarray:
    from sklearn.decomposition import TruncatedSVD
    from sklearn.feature_extraction.text import TfidfVectorizer

    vectorizer = TfidfVectorizer(
        ngram_range=(1, 2), stop_words="english", sublinear_tf=True, min_df=1, max_features=20000
    )
    try:
        tfidf = vectorizer.fit_transform([str(t) for t in texts])
    except ValueError as exc:
        if "empty vocabulary" not in str(exc):
            raise
        return np.zeros((len(texts), 1), dtype=np.float32)  # only stop words: nothing to compare
    n_components = min(128, tfidf.shape[1] - 1, len(texts) - 1)
    if n_components < 2:
        return _normalise(tfidf.toarray())
    svd = TruncatedSVD(n_components=n_components, random_state=42)
    return _normalise(svd.fit_transform(tfidf))


def _sentence_embed(
    texts: list[str], hashes: list[str], model_name: str, session: Session | None
) -> tuple[np.ndarray, int]:
    cached: dict[str, np.ndarray] = {}
    if session is not None:
        unique = list(set(hashes))
        for start in range(0, len(unique), 500):
            rows = session.scalars(
                select(EmbeddingCache).where(
                    EmbeddingCache.model == model_name,
                    EmbeddingCache.text_hash.in_(unique[start : start + 500]),
                )
            )
            for row in rows:
                cached[row.text_hash] = np.frombuffer(row.vector, dtype=np.float32)

    missing = {}
    for text, h in zip(texts, hashes, strict=True):
        if h not in cached and h not in missing:
            missing[h] = text
    if missing:
        model = load_sentence_model(model_name)
        encoded = model.encode(
            list(missing.values()),
            batch_size=64,
            show_progress_bar=False,
            normalize_embeddings=True,
            convert_to_numpy=True,
        ).astype(np.float32)
        for h, vec in zip(missing.keys(), encoded, strict=True):
            cached[h] = vec
            if session is not None:
                session.add(EmbeddingCache(text_hash=h, model=model_name, dim=len(vec), vector=vec.tobytes()))
        if session is not None:
            session.flush()
    hits = len(set(hashes)) - len(missing)
    return _normalise(np.vstack([cached[h] for h in hashes])), hits


def embed_texts(
    texts: list[str], hashes: list[str], backend: str, model_name: str, session: Session | None = None
) -> EmbeddingResult:
    if not texts:
        return EmbeddingResult(np.zeros((0, 1), dtype=np.float32), "none")
    if backend in ("auto", "sentence-transformers"):
        try:
            vectors, hits = _sentence_embed(texts, hashes, model_name, session)
            short = model_name.split("/")[-1]
            return EmbeddingResult(vectors, f"sentence-transformers:{short}", cache_hits=hits)
        except EmbeddingUnavailable as exc:
            reason = str(exc)
            log.warning("embedding fallback", extra={"ctx": {"reason": reason[:300]}})
            return EmbeddingResult(tfidf_embed(texts), "tfidf-lsa", fallback_reason=reason[:500])
    return EmbeddingResult(tfidf_embed(texts), "tfidf-lsa")
