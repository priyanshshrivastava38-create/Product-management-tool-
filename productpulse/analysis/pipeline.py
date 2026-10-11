"""End-to-end analysis run: sentiment → embeddings → themes → similarity → trends."""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable
from datetime import datetime

import numpy as np
from pydantic import BaseModel, Field
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from productpulse.analysis import clustering, embeddings, labeling, sentiment, similarity, trends
from productpulse.config import Settings
from productpulse.db.models import (
    AnalysisRun,
    Feedback,
    SimilarityPair,
    Theme,
    ThemeMembership,
    utcnow,
)
from productpulse.logging_setup import get_logger
from productpulse.services import audit

log = get_logger("pipeline")
MIN_WORDS_FOR_CLUSTERING = 3
DEFAULT_DISTANCE = {"semantic": 0.65, "lexical": 0.95}
Progress = Callable[[float, str], None]


class AnalysisParams(BaseModel):
    embedding_backend: str | None = None  # None → settings.embedding_backend
    algorithm: str = Field(default="auto", pattern="^(auto|agglomerative|hdbscan)$")
    # Cosine distance cut for agglomerative clustering; None → backend default
    # (0.65 for sentence embeddings, 0.95 for TF-IDF/LSA — see docs/AI_EVALUATION.md).
    distance_threshold: float | None = Field(default=None, gt=0.05, lt=1.5)
    min_cluster_size: int = Field(default=4, ge=2, le=200)
    similarity_threshold: float = Field(default=0.9, ge=0.5, le=0.999)
    trend: trends.TrendParams = Field(default_factory=trends.TrendParams)


def _noop(_p: float, _m: str) -> None:
    pass


def problem_statement(keywords: list[str], members: list[Feedback]) -> str:
    """Deterministic, template-based statement built only from computed metrics."""
    n = len(members)
    if n == 0:
        return "No supporting feedback."
    customers = {m.customer_id for m in members if m.customer_id}
    neg = sum(1 for m in members if m.sentiment_label == "negative")
    kw = list(keywords[:3])
    topic = ", ".join(f"“{k}”" for k in kw[:-1]) + (
        f" and “{kw[-1]}”" if len(kw) > 1 else (f"“{kw[0]}”" if kw else "a shared topic")
    )
    who = (
        f"{len(customers)} customer{'s' if len(customers) != 1 else ''} raised this " if customers else "This came up "
    )
    parts = [
        f"{who}{n} time{'s' if n != 1 else ''}, mostly mentioning {topic}.",
        f"{round(100 * neg / n)}% of these comments are negative.",
    ]
    area = Counter(m.product_area for m in members if m.product_area).most_common(1)
    seg = Counter(m.customer_segment for m in members if m.customer_segment).most_common(1)
    if area and seg:
        parts.append(
            f"It's mostly about {area[0][0]} ({round(100 * area[0][1] / n)}%) and comes most often from "
            f"{seg[0][0]} customers ({round(100 * seg[0][1] / n)}%)."
        )
    elif area:
        parts.append(f"It's mostly about {area[0][0]} ({round(100 * area[0][1] / n)}%).")
    elif seg:
        parts.append(f"It comes most often from {seg[0][0]} customers ({round(100 * seg[0][1] / n)}%).")
    return " ".join(parts)


def refresh_theme_metrics(session: Session, themes: list[Theme], trend_params: trends.TrendParams) -> None:
    """Recompute keywords, auto label, problem statement and trend for the given themes."""
    if not themes:
        return
    dates = [d for d in session.scalars(select(Feedback.created_at).where(Feedback.created_at.is_not(None)))]
    start, end = trends.dataset_span(dates)
    active = list(session.scalars(select(Theme).where(Theme.is_active.is_(True))))
    docs = {t.id: [m.feedback.text_normalized for m in t.memberships] for t in active}
    keywords = labeling.theme_keywords(docs)
    for theme in themes:
        members = [m.feedback for m in theme.memberships]
        theme.keywords = keywords.get(theme.id, theme.keywords or [])
        theme.auto_label = labeling.label_from_keywords(theme.keywords, f"Theme {theme.id}")
        if not theme.label_edited:
            theme.label = theme.auto_label
        theme.problem_statement = problem_statement(theme.keywords, members)
        theme.trend = trends.compute_trend([m.created_at for m in members], start, end, trend_params)
        if theme.ai_summary and theme.ai_cited_ids:
            member_ids = {m.id for m in members}
            if not set(theme.ai_cited_ids) <= member_ids:  # evidence changed → summary is stale
                theme.ai_summary, theme.ai_cited_ids, theme.ai_provider = None, None, None


def run_analysis(
    session: Session,
    settings: Settings,
    params: AnalysisParams | None = None,
    progress: Progress | None = None,
) -> AnalysisRun:
    params = params or AnalysisParams()
    progress = progress or _noop
    backend = params.embedding_backend or settings.embedding_backend

    run = AnalysisRun(params=params.model_dump(), status="running")
    session.add(run)
    session.flush()

    records = list(
        session.scalars(
            select(Feedback)
            .order_by(Feedback.created_at.desc().nulls_last(), Feedback.id)
            .limit(settings.max_analysis_rows)
        )
    )
    total = session.query(Feedback).count()
    notes = []
    if total > len(records):
        notes.append(
            f"Analysed the {len(records):,} most recent of {total:,} records (PRODUCTPULSE_MAX_ANALYSIS_ROWS)."
        )

    # 1. Sentiment (all records)
    progress(0.05, "Scoring sentiment")
    for fb in records:
        res = sentiment.classify(fb.text_normalized)
        fb.sentiment_label, fb.sentiment_score, fb.sentiment_reliability = res.label, res.score, res.reliability
        fb.analyzable = sentiment.word_count(fb.text_normalized) >= MIN_WORDS_FOR_CLUSTERING

    usable = [fb for fb in records if fb.analyzable]
    run.n_records = len(usable)
    run.n_excluded = len(records) - len(usable)
    if run.n_excluded:
        notes.append(f"{run.n_excluded} record(s) with fewer than {MIN_WORDS_FOR_CLUSTERING} words were not clustered.")

    # 2. Embeddings
    progress(0.15, "Embedding feedback (first run may download the model)")
    emb = embeddings.embed_texts(
        [fb.text_normalized for fb in usable],
        [fb.text_hash for fb in usable],
        backend,
        settings.embedding_model,
        session,
    )
    run.embedding_method = emb.method
    if emb.fallback_reason:
        notes.append(f"Semantic model unavailable, used TF-IDF fallback: {emb.fallback_reason}")

    # 3. Clustering
    progress(0.55, "Clustering into themes")
    threshold = params.distance_threshold or DEFAULT_DISTANCE["semantic" if emb.is_semantic else "lexical"]
    run.params = {**params.model_dump(), "distance_threshold_used": threshold}
    result = clustering.cluster_vectors(emb.vectors, params.algorithm, threshold, params.min_cluster_size)
    run.clustering_method = result.algorithm
    if result.note:
        notes.append(result.note)
    sims, cohesion = clustering.centroid_similarities(emb.vectors, result.labels) if len(usable) else (np.zeros(0), {})

    previous = list(session.scalars(select(Theme).where(Theme.is_active.is_(True), Theme.run_id != run.id)))
    previous_members = {t.id: {m.feedback_id for m in t.memberships} for t in previous}
    session.execute(update(Theme).where(Theme.is_active.is_(True), Theme.run_id != run.id).values(is_active=False))

    new_themes: dict[int, Theme] = {}
    for lab in range(result.n_clusters):
        theme = Theme(
            run_id=run.id,
            label=f"Theme {lab + 1}",
            auto_label=f"Theme {lab + 1}",
            cohesion=round(cohesion.get(lab, 0.0), 4),
            is_active=True,
        )
        session.add(theme)
        new_themes[lab] = theme
    session.flush()
    for idx, fb in enumerate(usable):
        lab = int(result.labels[idx])
        if lab >= 0:
            new_themes[lab].memberships.append(
                ThemeMembership(feedback=fb, centroid_similarity=round(float(sims[idx]), 4))
            )
    session.flush()

    # Carry over manual names when a new theme substantially matches an edited one (Jaccard >= 0.5).
    carried = 0
    for theme in new_themes.values():
        members = {m.feedback_id for m in theme.memberships}
        best, best_j = None, 0.0
        for old in previous:
            if not old.label_edited:
                continue
            old_m = previous_members[old.id]
            j = len(members & old_m) / max(len(members | old_m), 1)
            if j > best_j:
                best, best_j = old, j
        if best is not None and best_j >= 0.5:
            theme.label, theme.label_edited = best.label, True
            carried += 1
    if carried:
        notes.append(f"Carried over {carried} manually edited theme name(s) from the previous run.")

    progress(0.7, "Labelling themes and computing trends")
    refresh_theme_metrics(session, list(new_themes.values()), params.trend)

    # 4. Similarity pairs
    progress(0.8, "Finding duplicate and similar requests")
    pairs = similarity.find_similar_pairs(emb.vectors, [fb.text_hash for fb in usable], params.similarity_threshold)
    for p in pairs:
        a, b = usable[p.i], usable[p.j]
        # Order so that "a" is the earlier record (the original).
        if (b.created_at or datetime.max) < (a.created_at or datetime.max):
            a, b = b, a
        session.add(SimilarityPair(run_id=run.id, feedback_a_id=a.id, feedback_b_id=b.id, score=p.score, kind=p.kind))
    run.n_similar_pairs = len(pairs)

    # 5. Snapshot statistics for historical comparison
    run.n_themes = result.n_clusters
    run.n_unclustered = int((result.labels < 0).sum()) if len(usable) else 0
    counts = Counter(fb.sentiment_label for fb in records)
    run.stats = {
        "sentiment": {k: counts.get(k, 0) for k in ("positive", "neutral", "negative")},
        "themes": {t.label: len(t.memberships) for t in new_themes.values()},
        "emerging": [t.label for t in new_themes.values() if t.trend.get("status") == "emerging"],
        "embedding_cache_hits": emb.cache_hits,
    }
    run.notes = "\n".join(notes)
    run.status = "completed"
    run.finished_at = utcnow()
    audit.record(
        session,
        "analysis_run",
        "analysis_run",
        run.id,
        f"Analysis run #{run.id}: {run.n_themes} themes, {run.n_unclustered} unclustered, "
        f"{run.n_similar_pairs} similar pairs ({run.embedding_method}, {run.clustering_method}).",
        {"params": run.params},
    )
    session.flush()
    progress(1.0, "Done")
    log.info(
        "analysis completed", extra={"ctx": {"run_id": run.id, "themes": run.n_themes, "method": run.embedding_method}}
    )
    return run
