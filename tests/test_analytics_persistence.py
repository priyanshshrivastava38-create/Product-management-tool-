from __future__ import annotations

import pytest
from conftest import SMALL_SAMPLE
from sqlalchemy import func, select, text

from productpulse.analysis.pipeline import run_analysis
from productpulse.db import Database
from productpulse.db.models import AnalysisRun, AuditEvent, Feedback, ThemeMembership
from productpulse.db.session import SchemaVersionError
from productpulse.ingestion.csv_import import import_csv
from productpulse.services import analytics, themes, workspace
from productpulse.services import feedback as fb
from productpulse.services import prioritization as pz


def test_kpis_on_empty_workspace(db):
    with db.session() as s:
        k = analytics.kpis(s)
        assert k["total"] == 0 and k["themes"] == 0 and k["start"] is None and k["span_days"] == 0
        assert workspace.is_empty(s)
        assert analytics.runs_frame(s).empty
        assert analytics.traceability_links(s).empty
        assert pz.score_history(s).empty
        df, total = fb.search(s, fb.FeedbackFilter())
        assert total == 0 and df.empty


def test_kpis_and_series_after_analysis(loaded_db, settings):
    with loaded_db.session() as s:
        run_analysis(s, settings)
    with loaded_db.session() as s:
        k = analytics.kpis(s)
        assert k["total"] == s.scalar(select(func.count()).select_from(Feedback))
        assert k["positive"] + k["neutral"] + k["negative"] == k["total"]
        assert k["in_theme"] == len(set(s.scalars(select(ThemeMembership.feedback_id))))
        assert analytics.has_history(k["span_days"])
        df = fb.all_feedback_frame(s)
        vol = analytics.volume_over_time(df, "sentiment")
        assert vol["count"].sum() == df["created_at"].notna().sum()
        ch = analytics.theme_changes(themes.active_themes(s))
        assert len(ch) == k["themes"]


def test_insufficient_history_is_reported(db, settings):
    data = b"feedback_text,created_at\nExport is broken badly,2026-09-01\nExport fails again today,2026-09-02\n"
    with db.session() as s:
        import_csv(s, data, "x.csv", settings)
    with db.session() as s:
        k = analytics.kpis(s)
        assert not analytics.has_history(k["span_days"])


def test_compare_analysis_runs(loaded_db, settings):
    with loaded_db.session() as s:
        run_analysis(s, settings)
    with loaded_db.session() as s:
        run_analysis(s, settings.model_copy(update={}), None)
    with loaded_db.session() as s:
        a, b = s.scalars(select(AnalysisRun).order_by(AnalysisRun.id)).all()
        cmp = analytics.compare_runs(a, b)
        assert not cmp.empty and (cmp["Δ records"] == 0).all()  # deterministic: identical reruns


def test_search_filters_and_pagination(loaded_db, settings):
    with loaded_db.session() as s:
        run_analysis(s, settings)
    with loaded_db.session() as s:
        total = fb.count(s)
        p1, t1 = fb.search(s, fb.FeedbackFilter(), page=1, page_size=25)
        p2, _ = fb.search(s, fb.FeedbackFilter(), page=2, page_size=25)
        assert t1 == total and len(p1) == 25 and not set(p1["ID"]) & set(p2["ID"])
        hits, n = fb.search(s, fb.FeedbackFilter(query="csv export"))
        assert n > 0 and all("csv" in t.lower() and "export" in t.lower() for t in hits["Feedback"])
        neg, _ = fb.search(s, fb.FeedbackFilter(sentiments=["negative"]), page_size=200)
        assert set(neg["Sentiment"]) == {"negative"}
        # LIKE wildcards in user input are escaped, not interpreted
        assert fb.count(s, fb.FeedbackFilter(query="%")) == 0
        assert fb.count(s, fb.FeedbackFilter(query="'; DROP TABLE feedback; --")) == 0
        assert fb.count(s) == total
        unthemed = fb.count(s, fb.FeedbackFilter(theme_ids=[fb.UNTHEMED]))
        themed = sum(len(t.memberships) for t in themes.active_themes(s))
        assert unthemed + themed == total


def test_data_persists_across_connections(settings):
    db1 = Database(settings.database_url)
    with db1.session() as s:
        import_csv(s, SMALL_SAMPLE.read_bytes(), SMALL_SAMPLE.name, settings)
        oid = pz.create_opportunity(s, pz.OpportunityInput(name="Persist me", reach=1, effort=1)).id
    db1.engine.dispose()
    db2 = Database(settings.database_url)
    with db2.session() as s:
        assert s.scalar(select(func.count()).select_from(Feedback)) > 100
        assert s.get(type(pz.list_opportunities(s)[0]), oid).name == "Persist me"


def test_transaction_rolls_back_on_error(db):
    with pytest.raises(RuntimeError), db.session() as s:
        pz.create_opportunity(s, pz.OpportunityInput(name="Never saved", reach=1, effort=1))
        raise RuntimeError("boom")
    with db.session() as s:
        assert pz.list_opportunities(s) == []


def test_delete_batch_and_reset(loaded_db, settings):
    with loaded_db.session() as s:
        run_analysis(s, settings)
        batch = workspace.import_batches(s)[0]
        n = workspace.delete_batch(s, batch.id)
        assert n > 100
    with loaded_db.session() as s:
        assert s.scalar(select(func.count()).select_from(Feedback)) == 0
        assert s.scalar(select(func.count()).select_from(ThemeMembership)) == 0
        workspace.reset(s)
    with loaded_db.session() as s:
        events = list(s.scalars(select(AuditEvent)))
        assert [e.event_type for e in events] == ["workspace_reset"]


def test_schema_version_mismatch_is_detected(settings):
    db = Database(settings.database_url)
    with db.engine.begin() as conn:
        conn.execute(text("UPDATE schema_info SET version = 999"))
    with pytest.raises(SchemaVersionError):
        Database(settings.database_url)
