"""Render every Streamlit page in-process (AppTest) and fail on any exception.

Covers empty states and a fully populated workspace (analysis, opportunity,
scenario, roadmap item and version).
"""

from __future__ import annotations

import pytest
from conftest import ROOT
from streamlit.testing.v1 import AppTest

from productpulse.analysis.pipeline import run_analysis
from productpulse.config import get_settings
from productpulse.db import Database
from productpulse.services import prioritization as pz
from productpulse.services import roadmap as rm
from productpulse.services import themes, workspace

VIEWS = [
    "dashboard",
    "explorer",
    "insights",
    "prioritization",
    "roadmap",
    "analytics",
    "data_management",
    "settings_page",
]

SCRIPT = """
import sys
sys.path.insert(0, {root!r})
from productpulse.ui import theme
from productpulse.ui.views import {view}
theme.setup_page()
{view}.render()
"""


@pytest.fixture
def app_env(tmp_path, monkeypatch):
    url = f"sqlite:///{tmp_path / 'app.db'}"
    monkeypatch.setenv("PRODUCTPULSE_DATABASE_URL", url)
    monkeypatch.setenv("PRODUCTPULSE_EMBEDDING_BACKEND", "tfidf")
    get_settings.cache_clear()
    yield url
    get_settings.cache_clear()


def _render(view: str) -> AppTest:
    at = AppTest.from_string(SCRIPT.format(root=str(ROOT), view=view), default_timeout=120)
    at.run()
    return at


@pytest.mark.parametrize("view", VIEWS)
def test_pages_render_empty(app_env, view):
    at = _render(view)
    assert not at.exception, [e.value for e in at.exception]


def test_pages_render_with_data(app_env):
    settings = get_settings()
    db = Database(app_env)
    with db.session() as s:
        workspace.load_sample(s, settings)
        run_analysis(s, settings)
    with db.session() as s:
        t = themes.active_themes(s)[0]
        data, ev = pz.suggest_from_theme(s, t, "Strong")
        opp = pz.create_opportunity(s, data, ev, t.id)
        pz.create_opportunity(s, pz.OpportunityInput(name="Manual idea", reach=20, effort=2))
        pz.save_scenario(s, "Alt", pz.current_inputs(pz.list_opportunities(s)))
        rm.add_to_roadmap(s, [opp.id], "Now")
        rm.save_snapshot(s, "v1")
    for view in VIEWS:
        at = _render(view)
        assert not at.exception, (view, [e.value for e in at.exception])
    # Dashboard shows real computed values, not placeholders
    at = _render("dashboard")
    html = " ".join(m.value for m in at.markdown)
    assert "Biggest customer problems" in html and "Turn customer feedback into your next best" not in html


def test_replay_animation_has_a_frame_per_week(app_env):
    from productpulse.services import feedback as fb
    from productpulse.ui.views import common, dashboard

    settings = get_settings()
    db = Database(app_env)
    with db.session() as s:
        workspace.load_sample(s, settings)
        run_analysis(s, settings)
    with db.session() as s:
        df = fb.all_feedback_frame(s)
        summaries = common.theme_summaries(themes.active_themes(s))
        fig = dashboard._replay_figure(df, summaries)
        weeks = df.dropna(subset=["created_at"])["created_at"].dt.to_period("W").nunique()
        assert len(fig.frames) == weeks >= 20
        last = fig.frames[-1]
        total_last = sum(sum(tr.x) for tr in last.data)
        assert (
            total_last == sum(x["n"] for x in summaries) - df[df["theme_id"].notna() & df["created_at"].isna()].shape[0]
        )
