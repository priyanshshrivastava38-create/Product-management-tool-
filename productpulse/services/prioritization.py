"""Product opportunities, RICE scoring, sensitivity analysis and saved scenarios.

RICE = (Reach × Impact × Confidence) / Effort

- Reach: people/accounts affected per quarter (a number ≥ 0). When suggested from
  a theme it is a *proxy* (distinct customers in the evidence, else record count)
  and is flagged ``reach_is_proxy``.
- Impact: Intercom scale — 0.25 minimal, 0.5 low, 1 medium, 2 high, 3 massive.
- Confidence: a fraction in (0, 1]. Percentages (e.g. 80) are normalised to 0.8.
  Conventional anchors: 0.5 low, 0.8 medium, 1.0 high.
- Effort: person-months, strictly > 0 (prevents division by zero).
"""

from __future__ import annotations

from collections import Counter

import pandas as pd
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from productpulse.db.models import (
    Feedback,
    Opportunity,
    OpportunityEvidence,
    PriorityHistory,
    RoadmapItem,
    Scenario,
    Theme,
    utcnow,
)
from productpulse.services import audit

IMPACT_SCALE: dict[float, str] = {0.25: "Minimal", 0.5: "Low", 1.0: "Medium", 2.0: "High", 3.0: "Massive"}
CONFIDENCE_ANCHORS: dict[float, str] = {0.5: "Low", 0.8: "Medium", 1.0: "High"}
STATUSES = ("Proposed", "Under review", "Planned", "In progress", "Shipped", "Rejected")
RICE_FIELDS = ("reach", "impact", "confidence", "effort")


class PrioritizationError(ValueError):
    pass


class RiceInputs(BaseModel):
    reach: float = Field(ge=0, le=1e9)
    impact: float
    confidence: float
    effort: float = Field(gt=0, le=1000, description="person-months; must be positive")

    @field_validator("impact")
    @classmethod
    def _impact_on_scale(cls, v: float) -> float:
        if float(v) not in IMPACT_SCALE:
            raise ValueError(f"impact must be one of {sorted(IMPACT_SCALE)}")
        return float(v)

    @field_validator("confidence")
    @classmethod
    def _normalise_confidence(cls, v: float) -> float:
        v = float(v)
        if 1 < v <= 100:  # given as a percentage
            v = v / 100
        if not 0 < v <= 1:
            raise ValueError("confidence must be in (0, 1] or (0, 100]%")
        return round(v, 4)


def rice_score(inputs: RiceInputs) -> float:
    return round(inputs.reach * inputs.impact * inputs.confidence / inputs.effort, 2)


def score_of(opp: Opportunity) -> float:
    return rice_score(RiceInputs(reach=opp.reach, impact=opp.impact, confidence=opp.confidence, effort=opp.effort))


def formula_text(i: RiceInputs) -> str:
    return f"({i.reach:g} × {i.impact:g} × {i.confidence:.0%}) ÷ {i.effort:g} = {rice_score(i):,.2f}"


class OpportunityInput(BaseModel):
    name: str = Field(min_length=2, max_length=200)
    problem_statement: str = Field(default="", max_length=5000)
    proposed_solution: str = Field(default="", max_length=5000)
    business_rationale: str = Field(default="", max_length=5000)
    owner: str = Field(default="", max_length=100)
    status: str = "Proposed"
    target_release: str = Field(default="", max_length=100)
    product_area: str = Field(default="", max_length=100)
    segment: str = Field(default="", max_length=100)
    reach: float = Field(ge=0, le=1e9)
    reach_is_proxy: bool = False
    reach_basis: str = Field(default="", max_length=1000)
    impact: float = 1.0
    confidence: float = 0.8
    effort: float = 1.0

    @field_validator("name")
    @classmethod
    def _strip(cls, v: str) -> str:
        v = " ".join(v.split())
        if len(v) < 2:
            raise ValueError("name must be at least 2 characters")
        return v

    @field_validator("status")
    @classmethod
    def _status(cls, v: str) -> str:
        if v not in STATUSES:
            raise ValueError(f"status must be one of {STATUSES}")
        return v

    def rice(self) -> RiceInputs:
        return RiceInputs(reach=self.reach, impact=self.impact, confidence=self.confidence, effort=self.effort)


def _history(session: Session, opp: Opportunity, reason: str) -> None:
    session.add(
        PriorityHistory(
            opportunity_id=opp.id,
            reach=opp.reach,
            impact=opp.impact,
            confidence=opp.confidence,
            effort=opp.effort,
            score=score_of(opp),
            reason=reason[:200],
        )
    )


def _validated(data: OpportunityInput) -> tuple[OpportunityInput, RiceInputs]:
    rice = data.rice()  # raises ValidationError for bad impact/confidence/effort
    return data.model_copy(update={"confidence": rice.confidence, "impact": rice.impact}), rice


def create_opportunity(
    session: Session, data: OpportunityInput, evidence_ids: list[str] | None = None, theme_id: int | None = None
) -> Opportunity:
    data, _ = _validated(data)
    evidence_ids = list(dict.fromkeys(evidence_ids or []))
    if evidence_ids:
        found = set(session.scalars(select(Feedback.id).where(Feedback.id.in_(evidence_ids))))
        missing = [e for e in evidence_ids if e not in found]
        if missing:
            raise PrioritizationError(f"Evidence references unknown feedback: {missing[:5]}")
    theme = session.get(Theme, theme_id) if theme_id else None
    opp = Opportunity(
        **data.model_dump(), theme_id=theme.id if theme else None, theme_label=theme.label if theme else None
    )
    opp.evidence = [OpportunityEvidence(feedback_id=f) for f in evidence_ids]
    session.add(opp)
    session.flush()
    _history(session, opp, "created")
    audit.record(
        session,
        "opportunity_created",
        "opportunity",
        opp.id,
        f"Created opportunity “{opp.name}” (RICE {score_of(opp):,.2f}, {len(evidence_ids)} evidence records).",
        {"theme_id": opp.theme_id},
    )
    return opp


def update_opportunity(
    session: Session, opp_id: int, data: OpportunityInput, reason: str = "manual edit"
) -> Opportunity:
    opp = session.get(Opportunity, opp_id)
    if opp is None:
        raise PrioritizationError(f"Opportunity {opp_id} not found.")
    data, _ = _validated(data)
    before = {f: getattr(opp, f) for f in data.model_fields}
    for k, v in data.model_dump().items():
        setattr(opp, k, v)
    changed = {k: (before[k], getattr(opp, k)) for k in before if before[k] != getattr(opp, k)}
    if not changed:
        return opp
    opp.updated_at = utcnow()
    session.flush()
    rice_changed = any(k in changed for k in RICE_FIELDS)
    if rice_changed:
        _history(session, opp, reason)
    audit.record(
        session,
        "priority_updated" if rice_changed else "opportunity_updated",
        "opportunity",
        opp.id,
        f"Updated “{opp.name}”: " + ", ".join(f"{k} {a!s:.40} → {b!s:.40}" for k, (a, b) in changed.items()),
        {"changed": {k: [str(a), str(b)] for k, (a, b) in changed.items()}, "reason": reason},
    )
    return opp


def set_evidence(session: Session, opp_id: int, feedback_ids: list[str]) -> None:
    opp = session.get(Opportunity, opp_id)
    if opp is None:
        raise PrioritizationError("Opportunity not found.")
    ids = list(dict.fromkeys(feedback_ids))
    found = set(session.scalars(select(Feedback.id).where(Feedback.id.in_(ids)))) if ids else set()
    opp.evidence = [OpportunityEvidence(feedback_id=f) for f in ids if f in found]
    audit.record(
        session,
        "opportunity_evidence",
        "opportunity",
        opp.id,
        f"Linked {len(opp.evidence)} evidence record(s) to “{opp.name}”.",
    )


def delete_opportunity(session: Session, opp_id: int) -> None:
    opp = session.get(Opportunity, opp_id)
    if opp is None:
        return
    item = session.scalar(select(RoadmapItem).where(RoadmapItem.opportunity_id == opp_id))
    if item:
        session.delete(item)
    audit.record(session, "opportunity_deleted", "opportunity", opp_id, f"Deleted opportunity “{opp.name}”.")
    session.delete(opp)


def suggest_from_theme(session: Session, theme: Theme, quality_level: str) -> tuple[OpportunityInput, list[str]]:
    members = [m.feedback for m in theme.memberships]
    customers = {m.customer_id for m in members if m.customer_id}
    if customers:
        reach, basis = (
            len(customers),
            (
                f"PROXY: {len(customers)} distinct customer IDs in the theme's evidence. Feedback volume is not "
                "actual reach — replace with product analytics (e.g. accounts using the affected feature per quarter)."
            ),
        )
    else:
        reach, basis = (
            len(members),
            (
                f"PROXY: {len(members)} feedback records (no customer IDs available, so records may repeat customers). "
                "Replace with a measured reach estimate."
            ),
        )
    area = Counter(m.product_area for m in members if m.product_area).most_common(1)
    seg = Counter(m.customer_segment for m in members if m.customer_segment).most_common(1)
    data = OpportunityInput(
        name=theme.label[:200],
        problem_statement=theme.problem_statement[:5000],
        reach=reach,
        reach_is_proxy=True,
        reach_basis=basis,
        impact=1.0,
        confidence=0.8 if quality_level == "Strong" else 0.5,
        effort=1.0,
        product_area=area[0][0] if area else "",
        segment=seg[0][0] if seg else "",
    )
    return data, [m.id for m in members]


# ------------------------------------------------------------------------ ranking


def list_opportunities(session: Session) -> list[Opportunity]:
    return list(session.scalars(select(Opportunity).order_by(Opportunity.id)))


def ranking_frame(opps: list[Opportunity], overrides: dict[str, dict] | None = None) -> pd.DataFrame:
    rows = []
    for o in opps:
        vals = {f: getattr(o, f) for f in RICE_FIELDS}
        vals.update((overrides or {}).get(str(o.id), {}))
        try:
            score = rice_score(RiceInputs(**vals))
        except ValueError:
            score = float("nan")
        rows.append(
            {
                "id": o.id,
                "Opportunity": o.name,
                "Status": o.status,
                "Product area": o.product_area,
                "Segment": o.segment,
                "Reach": vals["reach"],
                "Reach is proxy": o.reach_is_proxy,
                "Impact": vals["impact"],
                "Confidence": vals["confidence"],
                "Effort": vals["effort"],
                "RICE": score,
                "Evidence": len(o.evidence),
                "Owner": o.owner,
                "Theme": o.theme_label,
            }
        )
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    df["Rank"] = df["RICE"].rank(ascending=False, method="min").astype("Int64")
    return df.sort_values(["Rank", "id"]).reset_index(drop=True)


def sensitivity(opps: list[Opportunity], pct: float = 0.25) -> pd.DataFrame:
    """Rank of each opportunity when one input of that opportunity moves ±pct (others fixed)."""
    base = ranking_frame(opps)
    if base.empty:
        return base
    base_rank = dict(zip(base["id"], base["Rank"], strict=True))
    out = []
    for o in opps:
        row = {"id": o.id, "Opportunity": o.name, "Base rank": int(base_rank[o.id])}
        worst, best = row["Base rank"], row["Base rank"]
        for f in RICE_FIELDS:
            for direction, mult in (("−", 1 - pct), ("+", 1 + pct)):
                val = getattr(o, f) * mult
                if f == "confidence":
                    val = min(max(val, 0.01), 1.0)
                if f == "impact":  # impact is ordinal: step one notch on the scale
                    scale = sorted(IMPACT_SCALE)
                    i = scale.index(o.impact)
                    val = scale[max(i - 1, 0)] if direction == "−" else scale[min(i + 1, len(scale) - 1)]
                rank = ranking_frame(opps, {str(o.id): {f: val}}).set_index("id").loc[o.id, "Rank"]
                row[f"{f.title()} {direction}"] = int(rank)
                worst, best = max(worst, int(rank)), min(best, int(rank))
        row["Rank range"] = f"{best}–{worst}"
        row["Stable"] = best == worst
        out.append(row)
    return pd.DataFrame(out).sort_values("Base rank").reset_index(drop=True)


def score_history(session: Session) -> pd.DataFrame:
    rows = session.execute(
        select(
            PriorityHistory.recorded_at,
            PriorityHistory.score,
            PriorityHistory.reason,
            Opportunity.name,
            PriorityHistory.opportunity_id,
        )
        .join(Opportunity)
        .order_by(PriorityHistory.recorded_at)
    ).all()
    return pd.DataFrame(rows, columns=["recorded_at", "score", "reason", "opportunity", "opportunity_id"])


# ---------------------------------------------------------------------- scenarios


def current_inputs(opps: list[Opportunity]) -> dict[str, dict]:
    return {str(o.id): {f: getattr(o, f) for f in RICE_FIELDS} for o in opps}


def save_scenario(session: Session, name: str, inputs: dict[str, dict], description: str = "") -> Scenario:
    name = " ".join(name.split())
    if not 2 <= len(name) <= 120:
        raise PrioritizationError("Scenario names must be 2–120 characters.")
    if session.scalar(select(Scenario).where(Scenario.name == name)):
        raise PrioritizationError(f"A scenario called “{name}” already exists.")
    clean = {}
    for oid, vals in inputs.items():
        clean[str(oid)] = RiceInputs(**{f: vals[f] for f in RICE_FIELDS}).model_dump()
    sc = Scenario(name=name, description=description[:2000], inputs=clean)
    session.add(sc)
    session.flush()
    audit.record(session, "scenario_saved", "scenario", sc.id, f"Saved prioritisation scenario “{name}”.")
    return sc


def list_scenarios(session: Session) -> list[Scenario]:
    return list(session.scalars(select(Scenario).order_by(Scenario.created_at.desc())))


def compare_scenarios(
    opps: list[Opportunity], a: dict[str, dict], b: dict[str, dict], label_a: str = "A", label_b: str = "B"
) -> pd.DataFrame:
    fa, fb = ranking_frame(opps, a), ranking_frame(opps, b)
    if fa.empty:
        return fa
    m = fa[["id", "Opportunity", "RICE", "Rank"]].merge(
        fb[["id", "RICE", "Rank"]], on="id", suffixes=(f" ({label_a})", f" ({label_b})")
    )
    m["Rank change"] = m[f"Rank ({label_a})"].astype(int) - m[f"Rank ({label_b})"].astype(int)
    return m.sort_values(f"Rank ({label_b})").reset_index(drop=True)


def apply_scenario(session: Session, scenario_id: int) -> int:
    sc = session.get(Scenario, scenario_id)
    if sc is None:
        raise PrioritizationError("Scenario not found.")
    n = 0
    for oid, vals in sc.inputs.items():
        opp = session.get(Opportunity, int(oid))
        if opp is None:
            continue  # opportunity deleted since the scenario was saved
        if any(getattr(opp, f) != vals[f] for f in RICE_FIELDS):
            for f in RICE_FIELDS:
                setattr(opp, f, vals[f])
            opp.updated_at = utcnow()
            session.flush()
            _history(session, opp, f"scenario restored: {sc.name}")
            n += 1
    audit.record(
        session,
        "scenario_restored",
        "scenario",
        sc.id,
        f"Restored scenario “{sc.name}” ({n} opportunity input set(s) changed).",
    )
    return n


def delete_scenario(session: Session, scenario_id: int) -> None:
    sc = session.get(Scenario, scenario_id)
    if sc:
        audit.record(session, "scenario_deleted", "scenario", sc.id, f"Deleted scenario “{sc.name}”.")
        session.delete(sc)
