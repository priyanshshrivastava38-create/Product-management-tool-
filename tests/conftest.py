from __future__ import annotations

import hashlib
import re
import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from productpulse.config import SAMPLE_DIR, Settings  # noqa: E402
from productpulse.db import Database  # noqa: E402
from productpulse.ingestion.csv_import import import_csv  # noqa: E402

SMALL_SAMPLE = SAMPLE_DIR / "sample_feedback.csv"
FULL_SAMPLE = SAMPLE_DIR / "synthetic_feedback.csv"
MESSY_SAMPLE = SAMPLE_DIR / "messy_feedback_example.csv"


@pytest.fixture
def settings(tmp_path) -> Settings:
    # TF-IDF keeps the default suite fast, offline and deterministic.
    return Settings(
        database_url=f"sqlite:///{tmp_path / 'test.db'}", embedding_backend="tfidf", llm_provider="none", llm_api_key=""
    )


@pytest.fixture
def db(settings) -> Database:
    return Database(settings.database_url)


@pytest.fixture
def loaded_db(db, settings) -> Database:
    with db.session() as s:
        import_csv(s, SMALL_SAMPLE.read_bytes(), SMALL_SAMPLE.name, settings)
    return db


class FakeSentenceModel:
    """Deterministic stand-in for SentenceTransformer: hashed bag-of-words vectors."""

    dim = 64

    def __init__(self):
        self.calls = 0

    def encode(self, texts, **_kwargs):
        self.calls += 1
        out = np.zeros((len(texts), self.dim), dtype=np.float32)
        for i, t in enumerate(texts):
            for w in re.findall(r"[a-z]+", t.lower()):
                h = int(hashlib.md5(w.encode()).hexdigest(), 16)
                out[i, h % self.dim] += 1.0
        norms = np.linalg.norm(out, axis=1, keepdims=True)
        norms[norms == 0] = 1
        return out / norms


@pytest.fixture
def fake_model(monkeypatch) -> FakeSentenceModel:
    from productpulse.analysis import embeddings

    model = FakeSentenceModel()
    monkeypatch.setattr(embeddings, "load_sentence_model", lambda _name: model)
    return model


def csv_bytes(header: str, *rows: str) -> bytes:
    return ("\n".join([header, *rows]) + "\n").encode("utf-8")
