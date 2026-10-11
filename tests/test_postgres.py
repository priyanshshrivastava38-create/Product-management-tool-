"""Opt-in: run the core journey against PostgreSQL.

    PRODUCTPULSE_TEST_POSTGRES_URL=postgresql+psycopg2://user@localhost/empty_db pytest tests/test_postgres.py

The database must be empty and disposable — the test resets it.
"""

from __future__ import annotations

import os

import pytest
from sqlalchemy import func, select

from productpulse.analysis.pipeline import run_analysis
from productpulse.config import Settings
from productpulse.db import Database
from productpulse.db.models import Feedback, SimilarityPair, ThemeMembership
from productpulse.exports import briefs
from productpulse.services import feedback as fb
from productpulse.services import prioritization, roadmap, themes, workspace

URL = os.environ.get("PRODUCTPULSE_TEST_POSTGRES_URL")
pytestmark = pytest.mark.skipif(not URL, reason="set PRODUCTPULSE_TEST_POSTGRES_URL to run")


def test_journey_on_postgres():
    settings = Settings(database_url=URL, embedding_backend="tfidf")
    db = Database(settings.database_url)
    with db.session() as s:
        workspace.reset(s)
    with db.session() as s:
        n = workspace.load_sample(s, settings).inserted
        again = workspace.load_sample(s, settings)
        assert again.inserted == 0 and again.already_imported == n
        run_analysis(s, settings)
    with db.session() as s:
        t = themes.active_themes(s)[0]
        assert themes.verify_quotes(s, themes.insight(s, t)["quotes"])
        data, ev = prioritization.suggest_from_theme(s, t, "Strong")
        opp = prioritization.create_opportunity(s, data, ev, t.id)
        roadmap.add_to_roadmap(s, [opp.id], "Now")
        roadmap.save_snapshot(s, "pg")
        assert "## 3. Supporting evidence" in briefs.product_brief(s, opp)
        hits, total = fb.search(s, fb.FeedbackFilter(query="csv export", sentiments=["negative"]))
        assert total > 0
        assert fb.count(s, fb.FeedbackFilter(query="100%_")) == 0
    with db.session() as s:
        batch = workspace.import_batches(s)[-1]
        workspace.delete_batch(s, batch.id)
    with db.session() as s:
        for model in (Feedback, ThemeMembership, SimilarityPair):
            assert s.scalar(select(func.count()).select_from(model)) == 0
        workspace.reset(s)
    db.engine.dispose()
