"""Features for regular use: last-visit digest, watchlist, remembered mappings, weekly summary."""

from __future__ import annotations

import hashlib
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from productpulse.db.models import AnalysisRun, AppState, Feedback, Theme, utcnow
from productpulse.services import analytics, audit, prioritization, roadmap, themes

LAST_VISIT = "last_visit"
WATCHLIST = "watchlist"
MAPPINGS = "column_mappings"
WATCH_MATCH = 0.3  # Jaccard overlap needed to follow a watched theme across re-analysis


# ------------------------------------------------------------------ key/value


def get(session: Session, key: str, default: Any = None) -> Any:
    row = session.get(AppState, key)
    return row.value.get("v", default) if row else default


def put(session: Session, key: str, value: Any) -> None:
    row = session.get(AppState, key)
    if row is None:
        session.add(AppState(key=key, value={"v": value}))
    else:
        row.value = {"v": value}
        row.updated_at = utcnow()


# ---------------------------------------------------------------- last visit


def touch_visit(session: Session) -> datetime | None:
    """Record a visit now; return the previous visit time (None on first visit)."""
    prev = get(session, LAST_VISIT)
    put(session, LAST_VISIT, utcnow().isoformat())
    return datetime.fromisoformat(prev) if prev else None


def since(session: Session, when: datetime | None) -> dict:
    """What changed since ``when`` and between the last two analysis runs."""
    out: dict = {"since": when, "new_feedback": 0, "new_themes": [], "grown": [], "shrunk": [], "rising": []}
    if when is not None:
        out["new_feedback"] = int(
            session.scalar(select(func.count()).select_from(Feedback).where(Feedback.imported_at > when)) or 0
        )
    runs = list(
        session.scalars(
            select(AnalysisRun).where(AnalysisRun.status == "completed").order_by(AnalysisRun.id.desc()).limit(2)
        )
    )
    active = themes.active_themes(session)
    out["rising"] = [t for t in active if (t.trend or {}).get("status") == "emerging"]
    if len(runs) == 2:
        # Match themes across runs by shared feedback (labels can change between runs).
        previous = list(session.scalars(select(Theme).where(Theme.run_id == runs[1].id, Theme.origin == "algorithm")))
        prev_sets = [({m.feedback_id for m in t.memberships}, t) for t in previous]
        for t in active:
            members = {m.feedback_id for m in t.memberships}
            best, best_j = None, 0.0
            for ps, pt in prev_sets:
                union = members | ps
                j = len(members & ps) / len(union) if union else 0.0
                if j > best_j:
                    best, best_j = pt, j
            n = len(members)
            if best is None or best_j < WATCH_MATCH:
                out["new_themes"].append((t.label, n))
            else:
                before = len(best.memberships)
                if n - before >= 3:
                    out["grown"].append((t.label, before, n))
                elif before - n >= 3:
                    out["shrunk"].append((t.label, before, n))
        out["grown"].sort(key=lambda x: -(x[2] - x[1]))
        out["compared_runs"] = (runs[1].id, runs[0].id)
    return out


# ---------------------------------------------------------------- watchlist


def _signature(theme: Theme) -> list[str]:
    return sorted(m.feedback_id for m in theme.memberships)


def watch(session: Session, theme_id: int) -> None:
    t = session.get(Theme, theme_id)
    if t is None:
        raise ValueError("Theme not found.")
    items = [w for w in get(session, WATCHLIST, []) if w["label"] != t.label]
    items.append(
        {"label": t.label, "members": _signature(t), "since": utcnow().isoformat(), "start_n": len(t.memberships)}
    )
    put(session, WATCHLIST, items)
    audit.record(session, "watch", "theme", t.id, f"Started watching “{t.label}”.")


def unwatch(session: Session, label: str) -> None:
    put(session, WATCHLIST, [w for w in get(session, WATCHLIST, []) if w["label"] != label])
    audit.record(session, "unwatch", "theme", "", f"Stopped watching “{label}”.")


def watched(session: Session) -> list[dict]:
    """Resolve each watched entry to the current active theme with the best membership overlap."""
    active = themes.active_themes(session)
    sigs = {t.id: set(_signature(t)) for t in active}
    out = []
    for w in get(session, WATCHLIST, []):
        members = set(w["members"])
        best, best_j = None, 0.0
        for t in active:
            if t.label == w["label"]:
                best, best_j = t, 1.0
                break
            union = members | sigs[t.id]
            j = len(members & sigs[t.id]) / len(union) if union else 0.0
            if j > best_j:
                best, best_j = t, j
        out.append({"entry": w, "theme": best if best_j >= WATCH_MATCH else None})
    return out


def is_watched(session: Session, theme: Theme) -> bool:
    return any(w["theme"] is not None and w["theme"].id == theme.id for w in watched(session))


# ------------------------------------------------------- remembered mappings


def header_key(headers: list[str]) -> str:
    return hashlib.sha256("|".join(h.strip().lower() for h in headers).encode()).hexdigest()[:16]


def remembered_mapping(session: Session, headers: list[str]) -> dict[str, str | None] | None:
    m = get(session, MAPPINGS, {}).get(header_key(headers))
    if not m:
        return None
    return {k: (v if v in headers else None) for k, v in m.items()}


def remember_mapping(session: Session, headers: list[str], mapping: dict[str, str | None]) -> None:
    all_maps = dict(get(session, MAPPINGS, {}))
    all_maps[header_key(headers)] = mapping
    put(session, MAPPINGS, all_maps)


# ------------------------------------------------------------ weekly summary


def weekly_summary(session: Session, days: int = 7) -> str:
    k = analytics.kpis(session)
    end = k["end"] or datetime.now()
    start = end - timedelta(days=days)
    recent = int(session.scalar(select(func.count()).select_from(Feedback).where(Feedback.created_at > start)) or 0)
    neg_recent = int(
        session.scalar(
            select(func.count())
            .select_from(Feedback)
            .where(Feedback.created_at > start, Feedback.sentiment_label == "negative")
        )
        or 0
    )
    active = themes.active_themes(session)
    lines = [f"**Customer feedback update — week ending {end:%b %d, %Y}**", ""]
    lines.append(
        f"• {recent:,} new pieces of feedback this week"
        + (f" ({round(100 * neg_recent / recent)}% negative)" if recent else "")
        + f"; {k['total']:,} in total."
    )
    rising = [t for t in active if (t.trend or {}).get("status") == "emerging"]
    if rising:
        lines.append(
            "• Rising fast: "
            + "; ".join(
                f"{t.label} ({t.trend.get('recent')} in {t.trend.get('window_days')} days, "
                f"{t.trend.get('growth') or 'new'}× baseline)"
                for t in rising[:3]
            )
        )
    if active:
        lines.append(
            "• Biggest problems: " + "; ".join(f"{t.label} ({len(t.memberships)} mentions)" for t in active[:3])
        )
    for w in watched(session):
        t = w["theme"]
        if t is not None:
            lines.append(
                f"• Watching “{t.label}”: {len(t.memberships)} mentions (was {w['entry']['start_n']} "
                "when you started watching)."
            )
    rank = prioritization.ranking_frame(prioritization.list_opportunities(session))
    if not rank.empty:
        top = rank.iloc[0]
        lines.append(f"• Top opportunity: {top['Opportunity']} (RICE {top['RICE']:,.1f}, {top['Evidence']} quotes).")
    now_items = [i for i in roadmap.items(session) if i.horizon == "Now"]
    if now_items:
        lines.append("• Building now: " + ", ".join(i.opportunity.name for i in now_items[:5]) + ".")
    lines += ["", "_Generated by ProductPulse AI from stored feedback. Every number links back to source records._"]
    return "\n".join(lines)
