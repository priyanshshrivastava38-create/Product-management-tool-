"""Aggregate metrics computed from database rows only — no hardcoded values."""

from __future__ import annotations

import pandas as pd
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from productpulse.db.models import (
    AnalysisRun,
    Feedback,
    Opportunity,
    OpportunityEvidence,
    RoadmapItem,
    Theme,
    ThemeMembership,
)

MIN_HISTORY_DAYS = 56  # need at least 8 weeks of data before showing growth comparisons


def kpis(session: Session) -> dict:
    total = session.scalar(select(func.count()).select_from(Feedback)) or 0
    dated = session.scalar(select(func.count()).select_from(Feedback).where(Feedback.created_at.is_not(None))) or 0
    sent = dict(
        session.execute(select(Feedback.sentiment_label, func.count()).group_by(Feedback.sentiment_label)).all()
    )
    themes = list(session.scalars(select(Theme).where(Theme.is_active.is_(True))))
    in_theme = (
        session.scalar(
            select(func.count(func.distinct(ThemeMembership.feedback_id))).join(Theme).where(Theme.is_active.is_(True))
        )
        or 0
    )
    n_opps = session.scalar(select(func.count()).select_from(Opportunity)) or 0
    opps_with_evidence = session.scalar(select(func.count(func.distinct(OpportunityEvidence.opportunity_id)))) or 0
    themes_linked = (
        session.scalar(
            select(func.count(func.distinct(Opportunity.theme_id))).where(
                Opportunity.theme_id.in_([t.id for t in themes]) if themes else False
            )
        )
        or 0
    )
    fb_linked = session.scalar(select(func.count(func.distinct(OpportunityEvidence.feedback_id)))) or 0
    n_road = session.scalar(select(func.count()).select_from(RoadmapItem)) or 0
    lo, hi = session.execute(select(func.min(Feedback.created_at), func.max(Feedback.created_at))).one()
    return {
        "total": total,
        "dated": dated,
        "undated": total - dated,
        "positive": sent.get("positive", 0),
        "neutral": sent.get("neutral", 0),
        "negative": sent.get("negative", 0),
        "unscored": sent.get(None, 0),
        "themes": len(themes),
        "emerging": sum(1 for t in themes if t.trend.get("status") == "emerging"),
        "in_theme": in_theme,
        "opportunities": n_opps,
        "opps_with_evidence": opps_with_evidence,
        "themes_linked": themes_linked,
        "feedback_linked": fb_linked,
        "roadmap_items": n_road,
        "start": lo,
        "end": hi,
        "span_days": (hi - lo).days if lo and hi else 0,
    }


def has_history(span_days: int) -> bool:
    return span_days >= MIN_HISTORY_DAYS


def volume_over_time(df: pd.DataFrame, by: str | None = None, freq: str = "W-MON") -> pd.DataFrame:
    d = df.dropna(subset=["created_at"])
    if d.empty:
        return pd.DataFrame(columns=["period", "count"] + ([by] if by else []))
    d = d.assign(period=d["created_at"].dt.to_period(freq.split("-")[0]).dt.start_time)
    keys = ["period"] + ([by] if by else [])
    out = d.fillna({by: "Unknown"} if by else {}).groupby(keys).size().reset_index(name="count")
    return out


def theme_changes(themes: list[Theme]) -> pd.DataFrame:
    rows = []
    for t in themes:
        tr = t.trend or {}
        rows.append(
            {
                "theme_id": t.id,
                "Theme": t.label,
                "Records": len(t.memberships),
                "Recent": tr.get("recent"),
                "Baseline avg": tr.get("baseline_mean"),
                "Change": (tr.get("recent") or 0) - (tr.get("baseline_mean") or 0)
                if tr.get("baseline_mean") is not None
                else None,
                "Growth ×": tr.get("growth"),
                "Status": tr.get("status", "—"),
                "Explanation": tr.get("explanation", ""),
            }
        )
    df = pd.DataFrame(rows)
    if not df.empty:
        df = df.sort_values("Change", ascending=False, na_position="last").reset_index(drop=True)
    return df


def traceability_links(session: Session) -> pd.DataFrame:
    """Theme → opportunity → roadmap horizon flows for a Sankey chart."""
    rows = session.execute(
        select(
            Opportunity.id,
            Opportunity.name,
            Opportunity.theme_label,
            RoadmapItem.horizon,
            func.count(OpportunityEvidence.id),
        )
        .outerjoin(RoadmapItem, RoadmapItem.opportunity_id == Opportunity.id)
        .outerjoin(OpportunityEvidence, OpportunityEvidence.opportunity_id == Opportunity.id)
        .group_by(Opportunity.id, Opportunity.name, Opportunity.theme_label, RoadmapItem.horizon)
    ).all()
    return pd.DataFrame(rows, columns=["opportunity_id", "opportunity", "theme", "horizon", "evidence"])


def runs_frame(session: Session) -> pd.DataFrame:
    runs = list(session.scalars(select(AnalysisRun).order_by(AnalysisRun.id.desc())))
    return pd.DataFrame(
        [
            {
                "Run": r.id,
                "Started": r.started_at,
                "Status": r.status,
                "Embedding": r.embedding_method,
                "Clustering": r.clustering_method,
                "Records": r.n_records,
                "Themes": r.n_themes,
                "Unclustered": r.n_unclustered,
                "Similar pairs": r.n_similar_pairs,
                "Negative": (r.stats or {}).get("sentiment", {}).get("negative"),
            }
            for r in runs
        ]
    )


def compare_runs(a: AnalysisRun, b: AnalysisRun) -> pd.DataFrame:
    ta, tb = (a.stats or {}).get("themes", {}), (b.stats or {}).get("themes", {})
    rows = []
    for name in sorted(set(ta) | set(tb)):
        rows.append(
            {
                "Theme": name,
                f"Run #{a.id}": ta.get(name),
                f"Run #{b.id}": tb.get(name),
                "Δ records": (tb.get(name) or 0) - (ta.get(name) or 0),
            }
        )
    return (
        pd.DataFrame(rows).sort_values("Δ records", ascending=False).reset_index(drop=True) if rows else pd.DataFrame()
    )
