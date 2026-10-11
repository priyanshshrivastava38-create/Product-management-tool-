"""Feedback queries: filtered search with pagination, detail and related records."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time

import pandas as pd
from sqlalchemy import Select, and_, func, or_, select
from sqlalchemy.orm import Session

from productpulse.db.models import (
    Feedback,
    Opportunity,
    OpportunityEvidence,
    SimilarityPair,
    Theme,
    ThemeMembership,
)

SORTS = {
    "Newest first": (Feedback.created_at.desc().nulls_last(), Feedback.id),
    "Oldest first": (Feedback.created_at.asc().nulls_last(), Feedback.id),
    "Most negative": (Feedback.sentiment_score.asc().nulls_last(), Feedback.id),
    "Most positive": (Feedback.sentiment_score.desc().nulls_last(), Feedback.id),
    "Highest customer value": (Feedback.customer_value.desc().nulls_last(), Feedback.id),
}
UNTHEMED = -1


@dataclass
class FeedbackFilter:
    query: str = ""
    start: date | None = None
    end: date | None = None
    sources: list[str] = field(default_factory=list)
    sentiments: list[str] = field(default_factory=list)
    product_areas: list[str] = field(default_factory=list)
    segments: list[str] = field(default_factory=list)
    theme_ids: list[int] = field(default_factory=list)  # UNTHEMED (-1) = not in any active theme
    include_undated: bool = True

    def apply(self, stmt: Select) -> Select:
        conds = []
        terms = [t for t in self.query.split() if t][:8]
        for term in terms:  # every term must match; LIKE with bound params (no SQL injection)
            escaped = term.lower().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            pattern = f"%{escaped}%"
            conds.append(
                or_(
                    func.lower(Feedback.text_normalized).like(pattern, escape="\\"),
                    func.lower(Feedback.id).like(pattern, escape="\\"),
                    func.lower(func.coalesce(Feedback.customer_id, "")).like(pattern, escape="\\"),
                )
            )
        if self.start or self.end:
            date_conds = []
            if self.start:
                date_conds.append(Feedback.created_at >= datetime.combine(self.start, time.min))
            if self.end:
                date_conds.append(Feedback.created_at <= datetime.combine(self.end, time.max))
            dated = and_(*date_conds)
            conds.append(or_(dated, Feedback.created_at.is_(None)) if self.include_undated else dated)
        for col, values in (
            (Feedback.source, self.sources),
            (Feedback.sentiment_label, self.sentiments),
            (Feedback.product_area, self.product_areas),
            (Feedback.customer_segment, self.segments),
        ):
            if values:
                conds.append(col.in_(values))
        if self.theme_ids:
            real = [t for t in self.theme_ids if t != UNTHEMED]
            active_members = select(ThemeMembership.feedback_id).join(Theme).where(Theme.is_active.is_(True))
            theme_conds = []
            if real:
                theme_conds.append(
                    Feedback.id.in_(select(ThemeMembership.feedback_id).where(ThemeMembership.theme_id.in_(real)))
                )
            if UNTHEMED in self.theme_ids:
                theme_conds.append(Feedback.id.not_in(active_members))
            conds.append(or_(*theme_conds))
        return stmt.where(*conds) if conds else stmt


def facets(session: Session) -> dict[str, list[str]]:
    out = {}
    for name, col in (
        ("sources", Feedback.source),
        ("product_areas", Feedback.product_area),
        ("segments", Feedback.customer_segment),
    ):
        out[name] = [v for v in session.scalars(select(col).distinct().where(col.is_not(None)).order_by(col))]
    return out


def date_bounds(session: Session) -> tuple[datetime | None, datetime | None]:
    row = session.execute(select(func.min(Feedback.created_at), func.max(Feedback.created_at))).one()
    return row[0], row[1]


def count(session: Session, flt: FeedbackFilter | None = None) -> int:
    stmt = select(func.count()).select_from(Feedback)
    if flt:
        stmt = flt.apply(stmt)
    return int(session.scalar(stmt) or 0)


def active_theme_map(session: Session, feedback_ids: list[str]) -> dict[str, tuple[int, str]]:
    if not feedback_ids:
        return {}
    rows = session.execute(
        select(ThemeMembership.feedback_id, Theme.id, Theme.label)
        .join(Theme)
        .where(Theme.is_active.is_(True), ThemeMembership.feedback_id.in_(feedback_ids))
    )
    return {fid: (tid, label) for fid, tid, label in rows}


def search(
    session: Session, flt: FeedbackFilter, page: int = 1, page_size: int = 50, sort: str = "Newest first"
) -> tuple[pd.DataFrame, int]:
    page_size = max(1, min(page_size, 200))
    total = count(session, flt)
    stmt = flt.apply(select(Feedback)).order_by(*SORTS.get(sort, SORTS["Newest first"]))
    rows = list(session.scalars(stmt.offset((max(page, 1) - 1) * page_size).limit(page_size)))
    themes = active_theme_map(session, [r.id for r in rows])
    df = pd.DataFrame(
        [
            {
                "ID": r.id,
                "Date": r.created_at,
                "Feedback": r.text_original,
                "Source": r.source,
                "Segment": r.customer_segment,
                "Product area": r.product_area,
                "Sentiment": r.sentiment_label,
                "Score": r.sentiment_score,
                "Theme": themes.get(r.id, (None, None))[1],
                "Customer": r.customer_id,
                "Duplicate of": r.duplicate_of_id,
            }
            for r in rows
        ]
    )
    return df, total


def get(session: Session, feedback_id: str) -> Feedback | None:
    return session.get(Feedback, feedback_id)


def related(session: Session, feedback_id: str, limit: int = 10) -> list[tuple[Feedback, float, str]]:
    """Similar/duplicate records from the latest analysis run that produced pairs for this record."""
    pairs = list(
        session.scalars(
            select(SimilarityPair)
            .where(or_(SimilarityPair.feedback_a_id == feedback_id, SimilarityPair.feedback_b_id == feedback_id))
            .order_by(SimilarityPair.run_id.desc(), SimilarityPair.score.desc())
        )
    )
    if not pairs:
        return []
    latest = pairs[0].run_id
    out = []
    for p in pairs:
        if p.run_id != latest:
            continue
        other = p.feedback_b_id if p.feedback_a_id == feedback_id else p.feedback_a_id
        fb = session.get(Feedback, other)
        if fb:
            out.append((fb, p.score, p.kind))
    return out[:limit]


def links(session: Session, feedback_id: str) -> dict:
    theme = session.execute(
        select(Theme.id, Theme.label)
        .join(ThemeMembership)
        .where(ThemeMembership.feedback_id == feedback_id, Theme.is_active.is_(True))
    ).first()
    opps = session.execute(
        select(Opportunity.id, Opportunity.name)
        .join(OpportunityEvidence)
        .where(OpportunityEvidence.feedback_id == feedback_id)
    ).all()
    return {"theme": tuple(theme) if theme else None, "opportunities": [tuple(o) for o in opps]}


def all_feedback_frame(session: Session) -> pd.DataFrame:
    """Analytics-friendly frame of every record (metadata only, no heavy joins)."""
    rows = session.execute(
        select(
            Feedback.id,
            Feedback.created_at,
            Feedback.source,
            Feedback.customer_segment,
            Feedback.product_area,
            Feedback.sentiment_label,
            Feedback.sentiment_score,
            Feedback.customer_id,
            Feedback.rating,
            Feedback.duplicate_of_id,
        )
    ).all()
    df = pd.DataFrame(
        rows,
        columns=[
            "id",
            "created_at",
            "source",
            "segment",
            "product_area",
            "sentiment",
            "sentiment_score",
            "customer_id",
            "rating",
            "duplicate_of_id",
        ],
    )
    df["created_at"] = pd.to_datetime(df["created_at"])
    themes = session.execute(
        select(ThemeMembership.feedback_id, Theme.id, Theme.label).join(Theme).where(Theme.is_active.is_(True))
    ).all()
    tmap = {fid: (tid, lab) for fid, tid, lab in themes}
    df["theme_id"] = df["id"].map(lambda f: tmap.get(f, (None, None))[0])
    df["theme"] = df["id"].map(lambda f: tmap.get(f, (None, None))[1])
    return df
