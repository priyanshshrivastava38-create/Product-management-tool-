from __future__ import annotations

import pytest
from pydantic import ValidationError
from sqlalchemy import func, select

from productpulse.analysis.pipeline import run_analysis
from productpulse.db.models import AuditEvent, Feedback, PriorityHistory
from productpulse.services import prioritization as pz
from productpulse.services import themes


def inp(**kw) -> pz.OpportunityInput:
    base = {"name": "Fix CSV export", "reach": 100, "impact": 2, "confidence": 0.8, "effort": 4}
    return pz.OpportunityInput(**(base | kw))


def test_rice_formula():
    assert pz.rice_score(pz.RiceInputs(reach=100, impact=2, confidence=0.8, effort=4)) == 40.0
    assert pz.rice_score(pz.RiceInputs(reach=0, impact=3, confidence=1, effort=1)) == 0.0
    text = pz.formula_text(pz.RiceInputs(reach=100, impact=2, confidence=0.8, effort=4))
    assert text == "(100 × 2 × 80%) ÷ 4 = 40.00"


def test_confidence_percentage_is_normalised():
    assert pz.RiceInputs(reach=10, impact=1, confidence=80, effort=1).confidence == 0.8


@pytest.mark.parametrize(
    "bad",
    [
        {"effort": 0},
        {"effort": -1},
        {"reach": -5},
        {"impact": 1.5},
        {"confidence": 0},
        {"confidence": 150},
    ],
)
def test_invalid_inputs_rejected(bad):
    with pytest.raises(ValidationError):
        pz.RiceInputs(**({"reach": 10, "impact": 1, "confidence": 0.5, "effort": 1} | bad))


def test_opportunity_input_validation():
    with pytest.raises(ValidationError):
        inp(name=" ")
    with pytest.raises(ValidationError):
        inp(status="Done-ish")


def test_create_update_records_history_and_audit(db):
    with db.session() as s:
        opp = pz.create_opportunity(s, inp())
        oid = opp.id
    with db.session() as s:
        pz.update_opportunity(s, oid, inp(effort=2))
        pz.update_opportunity(s, oid, inp(effort=2, owner="Priya"))  # non-RICE change: no new history row
    with db.session() as s:
        hist = list(s.scalars(select(PriorityHistory).where(PriorityHistory.opportunity_id == oid)))
        assert [h.score for h in hist] == [40.0, 80.0]
        events = [e.event_type for e in s.scalars(select(AuditEvent))]
        assert events.count("priority_updated") == 1 and "opportunity_updated" in events


def test_evidence_must_reference_real_feedback(loaded_db):
    with loaded_db.session() as s:
        with pytest.raises(pz.PrioritizationError, match="unknown feedback"):
            pz.create_opportunity(s, inp(), ["fb_does_not_exist"])
        real = s.scalars(select(Feedback.id).limit(3)).all()
        opp = pz.create_opportunity(s, inp(), real)
        assert len(opp.evidence) == 3


def test_ranking_ties_and_overrides(db):
    with db.session() as s:
        a = pz.create_opportunity(s, inp(name="Opp A", reach=100))
        pz.create_opportunity(s, inp(name="Opp B", reach=100))
        pz.create_opportunity(s, inp(name="Opp C", reach=10))
        opps = pz.list_opportunities(s)
        df = pz.ranking_frame(opps)
        assert list(df["Rank"]) == [1, 1, 3]
        df2 = pz.ranking_frame(opps, {str(a.id): {"effort": 1}})
        assert df2.iloc[0]["Opportunity"] == "Opp A" and df2.iloc[0]["RICE"] == 160.0


def test_sensitivity_flags_unstable_rankings(db):
    with db.session() as s:
        pz.create_opportunity(s, inp(name="Close 1", reach=100))
        pz.create_opportunity(s, inp(name="Close 2", reach=95))
        pz.create_opportunity(s, inp(name="Far", reach=5))
        sens = pz.sensitivity(pz.list_opportunities(s), 0.25)
        by = sens.set_index("Opportunity")
        assert not by.loc["Close 2", "Stable"]
        assert by.loc["Far", "Stable"]


def test_scenarios_save_compare_restore(db):
    with db.session() as s:
        a = pz.create_opportunity(s, inp(name="Opp A", reach=100))
        b = pz.create_opportunity(s, inp(name="Opp B", reach=50))
        opps = pz.list_opportunities(s)
        current = pz.current_inputs(opps)
        alt = {k: dict(v) for k, v in current.items()}
        alt[str(b.id)]["reach"] = 500
        sc = pz.save_scenario(s, "Enterprise push", alt)
        with pytest.raises(pz.PrioritizationError):
            pz.save_scenario(s, "Enterprise push", alt)
        cmp = pz.compare_scenarios(opps, current, alt, "now", "alt").set_index("Opportunity")
        assert cmp.loc["Opp B", "Rank (now)"] == 2 and cmp.loc["Opp B", "Rank (alt)"] == 1
        assert s.get(type(a), a.id).reach == 100  # saving does not change live inputs
        sid, bid = sc.id, b.id
    with db.session() as s:
        assert pz.apply_scenario(s, sid) == 1
    with db.session() as s:
        assert s.get(type(a), bid).reach == 500
        reasons = [h.reason for h in s.scalars(select(PriorityHistory).where(PriorityHistory.opportunity_id == bid))]
        assert reasons[-1] == "scenario restored: Enterprise push"


def test_suggest_from_theme_labels_reach_as_proxy(loaded_db, settings):
    with loaded_db.session() as s:
        run_analysis(s, settings)
    with loaded_db.session() as s:
        t = themes.active_themes(s)[0]
        data, evidence = pz.suggest_from_theme(s, t, "Strong")
        customers = {m.feedback.customer_id for m in t.memberships if m.feedback.customer_id}
        assert data.reach_is_proxy and data.reach == len(customers)
        assert "PROXY" in data.reach_basis
        assert sorted(evidence) == sorted(m.feedback_id for m in t.memberships)
        opp = pz.create_opportunity(s, data, evidence, t.id)
        assert opp.theme_label == t.label


def test_delete_opportunity_cascades(db):
    with db.session() as s:
        oid = pz.create_opportunity(s, inp()).id
    with db.session() as s:
        pz.delete_opportunity(s, oid)
    with db.session() as s:
        assert s.scalar(select(func.count()).select_from(PriorityHistory)) == 0
