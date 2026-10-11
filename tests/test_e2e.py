"""End-to-end smoke test of the core journey through the service layer (no UI).

import sample → analyse → discover problem → inspect evidence → create opportunity →
prioritise → roadmap → export brief → inspect history.
"""

from __future__ import annotations

from sqlalchemy import select

from productpulse.analysis.pipeline import run_analysis
from productpulse.db.models import AuditEvent, Feedback
from productpulse.exports import briefs
from productpulse.services import analytics, prioritization, roadmap, themes, workspace


def test_full_journey(db, settings):
    with db.session() as s:
        summary = workspace.load_sample(s, settings)
        assert summary.inserted > 1000 and summary.rejected == 0
    with db.session() as s:
        run = run_analysis(s, settings)
        assert run.n_themes >= 5
    with db.session() as s:
        top = themes.active_themes(s)[0]
        ins = themes.insight(s, top)
        assert ins["quotes"] and themes.verify_quotes(s, ins["quotes"])
        data, evidence = prioritization.suggest_from_theme(s, top, ins["quality"].level)
        opp = prioritization.create_opportunity(s, data, evidence, top.id)
        oid, first_score = opp.id, prioritization.score_of(opp)
    with db.session() as s:
        edited = data.model_copy(update={"impact": 2.0, "effort": 3.0, "proposed_solution": "Async export"})
        opp = prioritization.update_opportunity(s, oid, edited)
        assert prioritization.score_of(opp) == round(data.reach * 2 * data.confidence / 3, 2) != first_score
        roadmap.add_to_roadmap(s, [oid], "Now")
        roadmap.save_snapshot(s, "v1")
    with db.session() as s:
        opp = s.get(type(opp), oid)
        md = briefs.product_brief(s, opp)
        assert "Async export" in md and "RICE =" in md
        for e in opp.evidence[:5]:
            assert s.get(Feedback, e.feedback_id) is not None
        kinds = {e.event_type for e in s.scalars(select(AuditEvent))}
        assert {
            "import",
            "analysis_run",
            "opportunity_created",
            "priority_updated",
            "roadmap_added",
            "roadmap_snapshot",
        } <= kinds
        assert len(prioritization.score_history(s)) == 2
        k = analytics.kpis(s)
        assert k["opportunities"] == 1 and k["roadmap_items"] == 1 and k["themes_linked"] == 1
