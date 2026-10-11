"""Themes: listing, evidence-backed insights, and manual curation (rename/merge/split)."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from productpulse.analysis import trends
from productpulse.analysis.pipeline import refresh_theme_metrics
from productpulse.config import Settings
from productpulse.db.models import AnalysisRun, Feedback, SimilarityPair, Theme, ThemeMembership, utcnow
from productpulse.llm import provider
from productpulse.services import audit


class ThemeError(ValueError):
    pass


def latest_run(session: Session) -> AnalysisRun | None:
    return session.scalar(
        select(AnalysisRun).where(AnalysisRun.status == "completed").order_by(AnalysisRun.id.desc()).limit(1)
    )


def trend_params_for(run: AnalysisRun | None) -> trends.TrendParams:
    if run and isinstance(run.params, dict) and "trend" in run.params:
        return trends.TrendParams.model_validate(run.params["trend"])
    return trends.TrendParams()


def active_themes(session: Session) -> list[Theme]:
    themes = list(session.scalars(select(Theme).where(Theme.is_active.is_(True))))
    return sorted(themes, key=lambda t: (-len(t.memberships), t.id))


# ----------------------------------------------------------------- evidence quality


@dataclass
class EvidenceQuality:
    level: str  # Strong | Moderate | Limited
    points: int
    components: dict[str, str]


def evidence_quality(members: list[Feedback], cohesion: float, semantic: bool) -> EvidenceQuality:
    """Transparent 0–6 point rubric (see docs/PRODUCT_METRICS.md#evidence-quality)."""
    n = len(members)
    customers = len({m.customer_id for m in members if m.customer_id})
    sources = len({m.source for m in members if m.source})
    pts, comp = 0, {}
    vol = 2 if n >= 20 else 1 if n >= 8 else 0
    pts += vol
    comp["Volume"] = f"{n} records → {vol}/2"
    if customers:
        cust = 2 if customers >= 10 else 1 if customers >= 4 else 0
        comp["Customer breadth"] = f"{customers} distinct customers → {cust}/2"
    else:
        cust = 0
        comp["Customer breadth"] = "no customer IDs → 0/2"
    pts += cust
    src = 1 if sources >= 3 else 0
    pts += src
    comp["Source diversity"] = f"{sources} sources → {src}/1"
    bar = 0.55 if semantic else 0.35
    coh = 1 if cohesion >= bar else 0
    pts += coh
    comp["Cohesion"] = f"mean similarity to centroid {cohesion:.2f} (bar {bar}) → {coh}/1"
    level = "Strong" if pts >= 5 else "Moderate" if pts >= 3 else "Limited"
    return EvidenceQuality(level, pts, comp)


# --------------------------------------------------------------------------- insight


def representative_members(theme: Theme, k: int = 3) -> list[Feedback]:
    """Closest to centroid, skipping exact-text repeats. Quotes are the stored original text."""
    seen, out = set(), []
    for m in sorted(theme.memberships, key=lambda m: (-m.centroid_similarity, m.feedback_id)):
        if m.feedback.text_hash in seen:
            continue
        seen.add(m.feedback.text_hash)
        out.append(m.feedback)
        if len(out) >= k:
            break
    return out


def insight(session: Session, theme: Theme) -> dict:
    members = [m.feedback for m in theme.memberships]
    run = session.get(AnalysisRun, theme.run_id)
    semantic = bool(run and run.embedding_method.startswith("sentence-transformers"))
    sent = Counter(m.sentiment_label or "unscored" for m in members)
    ids = {m.id for m in members}
    pairs = (
        list(
            session.scalars(
                select(SimilarityPair)
                .where(
                    SimilarityPair.run_id == theme.run_id,
                    or_(SimilarityPair.feedback_a_id.in_(ids), SimilarityPair.feedback_b_id.in_(ids)),
                )
                .order_by(SimilarityPair.score.desc())
                .limit(50)
            )
        )
        if ids
        else []
    )
    return {
        "theme": theme,
        "members": members,
        "n": len(members),
        "customers": len({m.customer_id for m in members if m.customer_id}),
        "sentiment": {k: sent.get(k, 0) for k in ("negative", "neutral", "positive")},
        "product_areas": Counter(m.product_area for m in members if m.product_area).most_common(),
        "segments": Counter(m.customer_segment for m in members if m.customer_segment).most_common(),
        "sources": Counter(m.source for m in members if m.source).most_common(),
        "quotes": representative_members(theme),
        "pairs": pairs,
        "quality": evidence_quality(members, theme.cohesion, semantic),
        "run": run,
        "semantic": semantic,
        "total_value": sum(m.customer_value or 0 for m in members),
    }


def verify_quotes(session: Session, quotes: list[Feedback]) -> bool:
    """Every displayed quote must be byte-identical to a stored record."""
    for q in quotes:
        stored = session.get(Feedback, q.id)
        if stored is None or stored.text_original != q.text_original:
            return False
    return True


# ------------------------------------------------------------------------- curation


def _get_active(session: Session, theme_id: int) -> Theme:
    theme = session.get(Theme, theme_id)
    if theme is None or not theme.is_active:
        raise ThemeError(f"Theme {theme_id} does not exist or is no longer active.")
    return theme


def rename(session: Session, theme_id: int, new_label: str) -> Theme:
    new_label = " ".join(new_label.split())
    if not 2 <= len(new_label) <= 200:
        raise ThemeError("Theme names must be 2–200 characters.")
    theme = _get_active(session, theme_id)
    old = theme.label
    theme.label, theme.label_edited = new_label, True
    audit.record(session, "theme_renamed", "theme", theme.id, f"Renamed theme “{old}” → “{new_label}”.")
    return theme


def merge(session: Session, target_id: int, source_ids: list[int]) -> Theme:
    source_ids = [s for s in dict.fromkeys(source_ids) if s != target_id]
    if not source_ids:
        raise ThemeError("Choose at least one other theme to merge.")
    target = _get_active(session, target_id)
    existing = {m.feedback_id for m in target.memberships}
    names = []
    for sid in source_ids:
        src = _get_active(session, sid)
        names.append(src.label)
        for m in list(src.memberships):
            if m.feedback_id not in existing:
                target.memberships.append(
                    ThemeMembership(
                        feedback_id=m.feedback_id, centroid_similarity=m.centroid_similarity, assigned_by="manual_merge"
                    )
                )
                existing.add(m.feedback_id)
        src.is_active, src.merged_into_id = False, target.id
    total = len(target.memberships)
    target.cohesion = round(sum(m.centroid_similarity for m in target.memberships) / max(total, 1), 4)
    target.updated_at = utcnow()
    session.flush()
    refresh_theme_metrics(session, [target], trend_params_for(latest_run(session)))
    audit.record(
        session,
        "theme_merged",
        "theme",
        target.id,
        f"Merged {', '.join(f'“{n}”' for n in names)} into “{target.label}”.",
        {"sources": source_ids},
    )
    return target


def split(session: Session, theme_id: int, feedback_ids: list[str], new_label: str) -> Theme:
    theme = _get_active(session, theme_id)
    move = set(feedback_ids)
    members = {m.feedback_id: m for m in theme.memberships}
    if not move or not move <= set(members):
        raise ThemeError("Select records that belong to this theme.")
    if len(move) == len(members):
        raise ThemeError("Leave at least one record in the original theme (or rename it instead).")
    new = Theme(
        run_id=theme.run_id,
        label=new_label.strip() or "Split theme",
        auto_label="",
        label_edited=bool(new_label.strip()),
        origin="split",
        is_active=True,
    )
    session.add(new)
    for fid in move:
        m = members[fid]
        theme.memberships.remove(m)
        new.memberships.append(
            ThemeMembership(feedback_id=fid, centroid_similarity=m.centroid_similarity, assigned_by="manual_split")
        )
    session.flush()
    for t in (theme, new):
        t.cohesion = round(sum(m.centroid_similarity for m in t.memberships) / max(len(t.memberships), 1), 4)
    refresh_theme_metrics(session, [theme, new], trend_params_for(latest_run(session)))
    audit.record(
        session,
        "theme_split",
        "theme",
        new.id,
        f"Split {len(move)} record(s) from “{theme.label}” into “{new.label}”.",
        {"from_theme": theme.id},
    )
    return new


def remove_members(session: Session, theme_id: int, feedback_ids: list[str]) -> Theme:
    theme = _get_active(session, theme_id)
    drop = set(feedback_ids)
    for m in list(theme.memberships):
        if m.feedback_id in drop:
            theme.memberships.remove(m)
    session.flush()
    refresh_theme_metrics(session, [theme], trend_params_for(latest_run(session)))
    audit.record(
        session, "theme_members_removed", "theme", theme.id, f"Removed {len(drop)} record(s) from “{theme.label}”."
    )
    return theme


def review_pair(session: Session, pair_id: int, status: str) -> None:
    if status not in ("unreviewed", "confirmed", "dismissed"):
        raise ThemeError("Invalid review status.")
    pair = session.get(SimilarityPair, pair_id)
    if pair is None:
        raise ThemeError("Pair not found.")
    pair.review_status = status
    audit.record(
        session,
        "duplicate_reviewed",
        "similarity_pair",
        pair_id,
        f"Marked pair {pair.feedback_a_id} ↔ {pair.feedback_b_id} as {status}.",
    )


def generate_ai_summary(session: Session, settings: Settings, theme_id: int, caller=None) -> provider.SummaryOutcome:
    theme = _get_active(session, theme_id)
    ordered = sorted(theme.memberships, key=lambda m: -m.centroid_similarity)
    evidence = [(m.feedback.id, m.feedback.text_original) for m in ordered]
    outcome = provider.summarize_theme(settings, theme.label, evidence, theme.problem_statement, caller=caller)
    if not outcome.used_fallback:
        member_ids = {m.feedback_id for m in theme.memberships}
        assert set(outcome.cited_ids) <= member_ids  # validated in provider; defence in depth
        theme.ai_summary, theme.ai_cited_ids, theme.ai_provider = outcome.summary, outcome.cited_ids, outcome.provider
        audit.record(
            session,
            "ai_summary",
            "theme",
            theme.id,
            f"Generated AI summary for “{theme.label}” with {outcome.provider}.",
        )
    return outcome
