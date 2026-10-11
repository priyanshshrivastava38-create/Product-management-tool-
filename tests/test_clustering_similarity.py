from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from conftest import FULL_SAMPLE

from productpulse.analysis import clustering, embeddings, labeling, similarity

GROUPS = {
    "export": [
        "CSV export times out on big reports",
        "Exporting large reports to CSV fails",
        "Report CSV export hangs and never downloads",
        "CSV export of reports keeps failing",
    ],
    "sso": [
        "SSO login fails with Okta",
        "Single sign-on SSO login loops back",
        "SSO login through SAML is unreliable",
        "Users cannot login with SSO",
    ],
    "dark": [
        "Please add a dark mode theme",
        "Dark mode theme would be great",
        "We want a dark theme mode option",
        "Any plans for dark mode theme",
    ],
}


def _texts():
    texts, truth = [], []
    for k, v in GROUPS.items():
        texts += v
        truth += [k] * len(v)
    return texts, truth


def test_tiny_dataset_is_not_clustered():
    vecs = embeddings.tfidf_embed(["a b c", "d e f", "g h i"])
    res = clustering.cluster_vectors(vecs, min_cluster_size=3)
    assert (res.labels == -1).all()
    assert "needed to form themes" in res.note
    assert res.n_clusters == 0


def test_small_dataset_groups_obvious_topics():
    texts, truth = _texts()
    vecs = embeddings.tfidf_embed(texts)
    res = clustering.cluster_vectors(vecs, "agglomerative", 0.95, 3)
    assert res.n_clusters == 3
    for k in GROUPS:
        labs = {res.labels[i] for i, t in enumerate(truth) if t == k}
        assert len(labs) == 1 and -1 not in labs


def test_min_cluster_size_leaves_outliers_unclustered():
    texts, _ = _texts()
    texts = [*texts, "completely unrelated billing invoice question"]
    vecs = embeddings.tfidf_embed(texts)
    res = clustering.cluster_vectors(vecs, "agglomerative", 0.95, 3)
    assert res.labels[-1] == -1


def test_hdbscan_on_larger_dataset_runs_and_finds_themes():
    df = pd.read_csv(FULL_SAMPLE, keep_default_na=False).head(600)
    vecs = embeddings.tfidf_embed(df["feedback_text"].tolist())
    res = clustering.cluster_vectors(vecs, "hdbscan", min_cluster_size=8)
    assert res.algorithm == "hdbscan"
    assert res.n_clusters >= 5
    sims, coh = clustering.centroid_similarities(vecs, res.labels)
    assert set(coh) == set(range(res.n_clusters))
    assert np.all(sims[res.labels >= 0] > 0)


def test_auto_switches_by_size():
    vecs = embeddings.tfidf_embed([f"word{i} topic shared text" for i in range(20)])
    assert clustering.cluster_vectors(vecs, "auto").algorithm == "agglomerative"


def test_keywords_and_labels_are_from_the_data():
    kws = labeling.theme_keywords({0: GROUPS["export"], 1: GROUPS["sso"], 2: GROUPS["dark"]})
    assert "csv" in " ".join(kws[0])
    assert "sso" in " ".join(kws[1])
    assert "dark" in " ".join(kws[2])
    assert labeling.label_from_keywords(["csv export", "reports"], "x") == "CSV Export · Reports"
    assert labeling.label_from_keywords([], "Theme 7") == "Theme 7"


def test_similarity_exact_and_semantic():
    texts = ["Export to CSV fails", "Export to CSV fails", "CSV export fails for reports", "Love dark mode"]
    hashes = ["h1", "h1", "h2", "h3"]
    vecs = embeddings.tfidf_embed(texts)
    pairs = similarity.find_similar_pairs(vecs, hashes, threshold=0.3)
    kinds = {(p.i, p.j): p.kind for p in pairs}
    assert kinds[(0, 1)] == "exact"
    assert any(k == "semantic" and 3 not in ij for ij, k in kinds.items())
    assert all(3 not in (p.i, p.j) for p in pairs)  # unrelated text not paired
    strict = similarity.find_similar_pairs(vecs, hashes, threshold=0.999)
    assert [(p.i, p.j) for p in strict] == [(0, 1)]


def test_similarity_respects_top_k_and_cap():
    vecs = np.ones((10, 4), dtype=np.float32) / 2.0
    pairs = similarity.find_similar_pairs(vecs, [str(i) for i in range(10)], threshold=0.5, top_k=2, max_pairs=5)
    assert len(pairs) == 5


def test_embedding_fallback_when_model_unavailable(monkeypatch):
    def boom(_name):
        raise embeddings.EmbeddingUnavailable("no network")

    monkeypatch.setattr(embeddings, "load_sentence_model", boom)
    res = embeddings.embed_texts(["a b c", "d e f"], ["1", "2"], "auto", "x")
    assert res.method == "tfidf-lsa"
    assert "no network" in res.fallback_reason


def test_semantic_embeddings_are_cached(db, fake_model):
    texts = ["export fails", "sso broken", "export fails"]
    with db.session() as s:
        r1 = embeddings.embed_texts(texts, ["a", "b", "a"], "sentence-transformers", "fake/model", s)
    with db.session() as s:
        r2 = embeddings.embed_texts(texts, ["a", "b", "a"], "sentence-transformers", "fake/model", s)
    assert r1.method == "sentence-transformers:model"
    assert fake_model.calls == 1  # second call served entirely from the cache
    assert r2.cache_hits == 2
    np.testing.assert_allclose(r1.vectors, r2.vectors, atol=1e-6)


def test_zero_vectors_are_never_clustered():
    vecs = embeddings.tfidf_embed([*GROUPS["export"], *GROUPS["sso"], "the and of", "it is the", "of the and"])
    assert np.allclose(vecs[-3:], 0)  # stop-word-only texts carry no signal
    res = clustering.cluster_vectors(vecs, "agglomerative", 0.95, 3)
    assert (res.labels[-3:] == -1).all()


def test_tfidf_does_not_swallow_unexpected_errors():
    with pytest.raises(TypeError):
        embeddings.tfidf_embed(None)  # type: ignore[arg-type]
    assert embeddings.tfidf_embed(["the of and", "a an the"]).shape == (2, 1)  # empty vocabulary is handled


@pytest.mark.semantic
def test_real_sentence_model_groups_paraphrases():
    """Opt-in: PRODUCTPULSE_RUN_SEMANTIC=1 pytest -m semantic (downloads the model once)."""
    import os

    if os.environ.get("PRODUCTPULSE_RUN_SEMANTIC") != "1":
        pytest.skip("set PRODUCTPULSE_RUN_SEMANTIC=1 to run")
    texts = [
        "The app crashes when I open it",
        "Application closes immediately on launch",
        "Please add a dark theme",
        "A night mode would be lovely",
    ]
    res = embeddings.embed_texts(
        texts, [str(i) for i in range(4)], "sentence-transformers", "sentence-transformers/all-MiniLM-L6-v2"
    )
    sims = res.vectors @ res.vectors.T
    assert sims[0, 1] > sims[0, 2] and sims[2, 3] > sims[2, 0]  # paraphrases with no shared words
