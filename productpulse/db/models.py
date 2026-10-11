"""SQLAlchemy ORM models — the canonical data model.

See docs/ARCHITECTURE.md for an entity diagram. Feedback rows are never
modified by analysis except for derived sentiment columns; the original text is
kept verbatim in ``text_original``.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

SCHEMA_VERSION = 1


def utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


class Base(DeclarativeBase):
    type_annotation_map = {dict[str, Any]: JSON, list[Any]: JSON}  # noqa: RUF012 (SQLAlchemy API)


class SchemaInfo(Base):
    __tablename__ = "schema_info"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    version: Mapped[int] = mapped_column(Integer)
    initialised_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class ImportBatch(Base):
    __tablename__ = "import_batches"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    filename: Mapped[str] = mapped_column(String(255))
    file_sha256: Mapped[str] = mapped_column(String(64), index=True)
    encoding: Mapped[str] = mapped_column(String(32))
    column_mapping: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    total_rows: Mapped[int] = mapped_column(Integer, default=0)
    valid_rows: Mapped[int] = mapped_column(Integer, default=0)
    inserted_rows: Mapped[int] = mapped_column(Integer, default=0)
    already_imported_rows: Mapped[int] = mapped_column(Integer, default=0)
    rejected_rows: Mapped[int] = mapped_column(Integer, default=0)
    warning_count: Mapped[int] = mapped_column(Integer, default=0)
    is_sample: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Feedback(Base):
    __tablename__ = "feedback"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    external_id: Mapped[str | None] = mapped_column(String(255))
    text_original: Mapped[str] = mapped_column(Text)
    text_normalized: Mapped[str] = mapped_column(Text)
    text_hash: Mapped[str] = mapped_column(String(64), index=True)
    created_at: Mapped[datetime | None] = mapped_column(DateTime, index=True)
    source: Mapped[str | None] = mapped_column(String(100), index=True)
    customer_id: Mapped[str | None] = mapped_column(String(255), index=True)
    customer_segment: Mapped[str | None] = mapped_column(String(100), index=True)
    product_area: Mapped[str | None] = mapped_column(String(100), index=True)
    rating: Mapped[float | None] = mapped_column(Float)
    customer_value: Mapped[float | None] = mapped_column(Float)
    status: Mapped[str | None] = mapped_column(String(50))
    # Exact-text duplicate of an earlier record (kept, never deleted).
    duplicate_of_id: Mapped[str | None] = mapped_column(ForeignKey("feedback.id", ondelete="SET NULL"), index=True)
    import_batch_id: Mapped[int | None] = mapped_column(
        ForeignKey("import_batches.id", ondelete="SET NULL"), index=True
    )
    imported_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    # Derived by analysis
    sentiment_label: Mapped[str | None] = mapped_column(String(10), index=True)
    sentiment_score: Mapped[float | None] = mapped_column(Float)
    sentiment_reliability: Mapped[str | None] = mapped_column(String(10))
    analyzable: Mapped[bool | None] = mapped_column(Boolean)

    memberships: Mapped[list[ThemeMembership]] = relationship(
        back_populates="feedback", cascade="all, delete-orphan", passive_deletes=True
    )


class EmbeddingCache(Base):
    __tablename__ = "embedding_cache"
    __table_args__ = (UniqueConstraint("text_hash", "model", name="uq_embedding_text_model"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    text_hash: Mapped[str] = mapped_column(String(64))
    model: Mapped[str] = mapped_column(String(200))
    dim: Mapped[int] = mapped_column(Integer)
    vector: Mapped[bytes] = mapped_column(LargeBinary)


class AnalysisRun(Base):
    __tablename__ = "analysis_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime)
    status: Mapped[str] = mapped_column(String(20), default="running")
    embedding_method: Mapped[str] = mapped_column(String(200), default="")
    clustering_method: Mapped[str] = mapped_column(String(50), default="")
    params: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    n_records: Mapped[int] = mapped_column(Integer, default=0)
    n_excluded: Mapped[int] = mapped_column(Integer, default=0)
    n_themes: Mapped[int] = mapped_column(Integer, default=0)
    n_unclustered: Mapped[int] = mapped_column(Integer, default=0)
    n_similar_pairs: Mapped[int] = mapped_column(Integer, default=0)
    stats: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    notes: Mapped[str] = mapped_column(Text, default="")
    error: Mapped[str | None] = mapped_column(Text)

    themes: Mapped[list[Theme]] = relationship(back_populates="run")


class Theme(Base):
    __tablename__ = "themes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("analysis_runs.id", ondelete="CASCADE"), index=True)
    label: Mapped[str] = mapped_column(String(200))
    auto_label: Mapped[str] = mapped_column(String(200))
    keywords: Mapped[list[Any]] = mapped_column(JSON, default=list)
    problem_statement: Mapped[str] = mapped_column(Text, default="")
    cohesion: Mapped[float] = mapped_column(Float, default=0.0)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    label_edited: Mapped[bool] = mapped_column(Boolean, default=False)
    origin: Mapped[str] = mapped_column(String(20), default="algorithm")  # algorithm|merge|split
    merged_into_id: Mapped[int | None] = mapped_column(ForeignKey("themes.id", ondelete="SET NULL"))
    # Trend (computed by analysis, recomputed on edits)
    trend: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    # Optional AI interpretation — always stored separately from observed facts
    ai_summary: Mapped[str | None] = mapped_column(Text)
    ai_cited_ids: Mapped[list[Any] | None] = mapped_column(JSON)
    ai_provider: Mapped[str | None] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    run: Mapped[AnalysisRun] = relationship(back_populates="themes")
    memberships: Mapped[list[ThemeMembership]] = relationship(
        back_populates="theme", cascade="all, delete-orphan", passive_deletes=True
    )


class ThemeMembership(Base):
    __tablename__ = "theme_memberships"
    __table_args__ = (UniqueConstraint("theme_id", "feedback_id", name="uq_theme_feedback"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    theme_id: Mapped[int] = mapped_column(ForeignKey("themes.id", ondelete="CASCADE"), index=True)
    feedback_id: Mapped[str] = mapped_column(ForeignKey("feedback.id", ondelete="CASCADE"), index=True)
    centroid_similarity: Mapped[float] = mapped_column(Float, default=0.0)
    assigned_by: Mapped[str] = mapped_column(String(20), default="algorithm")

    theme: Mapped[Theme] = relationship(back_populates="memberships")
    feedback: Mapped[Feedback] = relationship(back_populates="memberships")


class SimilarityPair(Base):
    __tablename__ = "similarity_pairs"
    __table_args__ = (
        UniqueConstraint("run_id", "feedback_a_id", "feedback_b_id", name="uq_pair"),
        Index("ix_pair_a", "feedback_a_id"),
        Index("ix_pair_b", "feedback_b_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("analysis_runs.id", ondelete="CASCADE"), index=True)
    feedback_a_id: Mapped[str] = mapped_column(ForeignKey("feedback.id", ondelete="CASCADE"))
    feedback_b_id: Mapped[str] = mapped_column(ForeignKey("feedback.id", ondelete="CASCADE"))
    score: Mapped[float] = mapped_column(Float)
    kind: Mapped[str] = mapped_column(String(10))  # exact | semantic
    review_status: Mapped[str] = mapped_column(String(15), default="unreviewed")


class Opportunity(Base):
    __tablename__ = "opportunities"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    problem_statement: Mapped[str] = mapped_column(Text, default="")
    proposed_solution: Mapped[str] = mapped_column(Text, default="")
    theme_id: Mapped[int | None] = mapped_column(ForeignKey("themes.id", ondelete="SET NULL"))
    theme_label: Mapped[str | None] = mapped_column(String(200))
    reach: Mapped[float] = mapped_column(Float, default=0)
    reach_is_proxy: Mapped[bool] = mapped_column(Boolean, default=False)
    reach_basis: Mapped[str] = mapped_column(Text, default="")
    impact: Mapped[float] = mapped_column(Float, default=1.0)
    confidence: Mapped[float] = mapped_column(Float, default=0.8)
    effort: Mapped[float] = mapped_column(Float, default=1.0)
    business_rationale: Mapped[str] = mapped_column(Text, default="")
    owner: Mapped[str] = mapped_column(String(100), default="")
    status: Mapped[str] = mapped_column(String(30), default="Proposed", index=True)
    target_release: Mapped[str] = mapped_column(String(100), default="")
    product_area: Mapped[str] = mapped_column(String(100), default="")
    segment: Mapped[str] = mapped_column(String(100), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    evidence: Mapped[list[OpportunityEvidence]] = relationship(
        back_populates="opportunity", cascade="all, delete-orphan", passive_deletes=True
    )


class OpportunityEvidence(Base):
    __tablename__ = "opportunity_evidence"
    __table_args__ = (UniqueConstraint("opportunity_id", "feedback_id", name="uq_opp_feedback"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    opportunity_id: Mapped[int] = mapped_column(ForeignKey("opportunities.id", ondelete="CASCADE"), index=True)
    feedback_id: Mapped[str] = mapped_column(ForeignKey("feedback.id", ondelete="CASCADE"), index=True)

    opportunity: Mapped[Opportunity] = relationship(back_populates="evidence")


class PriorityHistory(Base):
    __tablename__ = "priority_history"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    opportunity_id: Mapped[int] = mapped_column(ForeignKey("opportunities.id", ondelete="CASCADE"), index=True)
    recorded_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    reach: Mapped[float] = mapped_column(Float)
    impact: Mapped[float] = mapped_column(Float)
    confidence: Mapped[float] = mapped_column(Float)
    effort: Mapped[float] = mapped_column(Float)
    score: Mapped[float] = mapped_column(Float)
    reason: Mapped[str] = mapped_column(String(200), default="")


class Scenario(Base):
    __tablename__ = "scenarios"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(120), unique=True)
    description: Mapped[str] = mapped_column(Text, default="")
    # {"<opp_id>": {"reach":..,"impact":..,"confidence":..,"effort":..}}
    inputs: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class RoadmapItem(Base):
    __tablename__ = "roadmap_items"
    __table_args__ = (UniqueConstraint("opportunity_id", name="uq_roadmap_opportunity"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    opportunity_id: Mapped[int] = mapped_column(ForeignKey("opportunities.id", ondelete="CASCADE"))
    horizon: Mapped[str] = mapped_column(String(30), default="Next")
    position: Mapped[int] = mapped_column(Integer, default=0)
    owner: Mapped[str] = mapped_column(String(100), default="")
    status: Mapped[str] = mapped_column(String(30), default="Planned")
    target_date: Mapped[datetime | None] = mapped_column(DateTime)
    dependencies: Mapped[str] = mapped_column(Text, default="")
    rationale: Mapped[str] = mapped_column(Text, default="")
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    opportunity: Mapped[Opportunity] = relationship()


class RoadmapSnapshot(Base):
    __tablename__ = "roadmap_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    note: Mapped[str] = mapped_column(Text, default="")
    items: Mapped[list[Any]] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)


class AuditEvent(Base):
    __tablename__ = "audit_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    event_type: Mapped[str] = mapped_column(String(50), index=True)
    entity_type: Mapped[str] = mapped_column(String(50))
    entity_id: Mapped[str] = mapped_column(String(50), default="")
    summary: Mapped[str] = mapped_column(Text)
    details: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class AppState(Base):
    """Small key → JSON store for preferences: last visit, watchlist, remembered column mappings.

    Added after schema v1; ``create_all`` adds it to existing databases (additive, no version bump).
    """

    __tablename__ = "app_state"

    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)
