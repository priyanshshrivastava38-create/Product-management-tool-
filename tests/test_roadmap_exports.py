from __future__ import annotations

from datetime import date

import pandas as pd
import pytest
from sqlalchemy import select

from productpulse.analysis.pipeline import run_analysis
from productpulse.db.models import Feedback, RoadmapItem
from productpulse.exports import briefs
from productpulse.services import prioritization as pz
from productpulse.services import roadmap as rm
from productpulse.services import themes


def _opps(s, n=3):
    return [
        pz.create_opportunity(
            s, pz.OpportunityInput(name=f"Opp {i}", reach=10 * (i + 1), impact=1, confidence=0.8, effort=1)
        )
        for i in range(n)
    ]


def test_add_move_update_remove(db):
    with db.session() as s:
        o = _opps(s)
        rm.add_to_roadmap(s, [o[0].id, o[1].id], "Now")
        rm.add_to_roadmap(s, [o[0].id], "Later")  # already on roadmap → ignored
        rm.add_to_roadmap(s, [o[2].id], "Q1 2027")  # custom horizon
        assert [i.opportunity.name for i in rm.items(s)] == ["Opp 0", "Opp 1", "Opp 2"]
        assert rm.horizons(s) == ["Now", "Next", "Later", "Q1 2027"]
        assert s.get(type(o[0]), o[0].id).status == "Planned"
        second = rm.items(s)[1]
        rm.move(s, second.id, -1)
        assert [i.opportunity.name for i in rm.items(s) if i.horizon == "Now"] == ["Opp 1", "Opp 0"]
        rm.update_item(
            s,
            second.id,
            horizon="Next",
            status="In progress",
            owner="Sam",
            target_date=date(2026, 12, 1),
            dependencies="Opp 0",
            rationale="Unblocks enterprise deals",
        )
        it = s.get(RoadmapItem, second.id)
        got = (it.horizon, it.status, it.owner, it.target_date.date())
        assert got == ("Next", "In progress", "Sam", date(2026, 12, 1))
        rm.update_item(s, second.id, clear_target_date=True)
        assert it.target_date is None
        with pytest.raises(rm.RoadmapError):
            rm.update_item(s, second.id, status="Finished")
        rm.remove(s, it.id)
        assert len(rm.items(s)) == 2


def test_snapshots_and_compare(db):
    with db.session() as s:
        o = _opps(s)
        rm.add_to_roadmap(s, [o[0].id, o[1].id], "Now")
        snap = rm.save_snapshot(s, "v1")
        items = rm.items(s)
        rm.update_item(s, items[0].id, horizon="Later")
        rm.add_to_roadmap(s, [o[2].id], "Next")
        bigger = pz.OpportunityInput(name="Opp 1", reach=999, impact=1, confidence=0.8, effort=1)
        pz.update_opportunity(s, o[1].id, bigger)
        diff = rm.compare(snap.items, rm.current_as_snapshot_items(s))
        changes = set(zip(diff["Initiative"], diff["Change"], strict=True))
        assert ("Opp 0", "Moved horizon") in changes
        assert ("Opp 2", "Added") in changes
        assert ("Opp 1", "RICE score") in changes
        back = rm.compare(rm.current_as_snapshot_items(s), snap.items)
        assert ("Opp 2", "Removed") in set(zip(back["Initiative"], back["Change"], strict=True))
        assert rm.compare(snap.items, snap.items).empty


def test_csv_safe_neutralises_formulas():
    df = pd.DataFrame({"a": ["=1+1", "+cmd", "-2", "@x", "normal", None], "n": [1, 2, 3, 4, 5, 6]})
    out = briefs.csv_safe(df)
    assert list(out["a"][:5]) == ["'=1+1", "'+cmd", "'-2", "'@x", "normal"]
    assert b"'=1+1" in briefs.to_csv_bytes(df)


def test_product_brief_has_all_sections_and_verbatim_quotes(loaded_db, settings):
    with loaded_db.session() as s:
        run_analysis(s, settings)
    with loaded_db.session() as s:
        t = themes.active_themes(s)[0]
        data, ev = pz.suggest_from_theme(s, t, "Moderate")
        opp = pz.create_opportunity(s, data, ev, t.id)
        md = briefs.product_brief(s, opp)
        for i, h in enumerate(
            [
                "Problem statement",
                "Target customer segment",
                "Supporting evidence",
                "Proposed opportunity",
                "Expected outcome",
                "Prioritisation rationale",
                "Assumptions and risks",
                "Success metrics",
                "Open questions",
            ],
            1,
        ):
            assert f"## {i}. {h}" in md
        assert "[Forecast]" in md and "proxy" in md.lower()
        quoted = [line[3:-1] for line in md.splitlines() if line.startswith("> “")]
        assert quoted
        stored = {f.text_original.replace("\n", " ").strip() for f in s.scalars(select(Feedback))}
        assert all(q in stored for q in quoted)
        rm.add_to_roadmap(s, [opp.id], "Now")
        road = briefs.roadmap_markdown(s, rm.items(s))
        assert "## Now" in road and opp.name in road


def test_brief_for_manual_idea_without_evidence(db):
    with db.session() as s:
        o = _opps(s, 1)[0]
        md = briefs.product_brief(s, o)
        assert "not yet evidence-backed" in md
        assert "Who owns this initiative?" in md


def test_apply_layout_from_drag_and_drop(db):
    with db.session() as s:
        o = _opps(s)
        rm.add_to_roadmap(s, [o[0].id, o[1].id, o[2].id], "Next")
        a, b, c = (i.id for i in rm.items(s))
        assert rm.apply_layout(s, {"Now": [c], "Next": [b, a], "Later": []}) == 3
        assert [(i.horizon, i.opportunity.name) for i in rm.items(s)] == [
            ("Now", "Opp 2"),
            ("Next", "Opp 1"),
            ("Next", "Opp 0"),
        ]
        assert rm.apply_layout(s, {"Now": [c], "Next": [b, a], "Later": []}) == 0  # no-op
        with pytest.raises(rm.RoadmapError):
            rm.apply_layout(s, {"Now": [c], "Next": [b]})  # an item went missing → stale board
