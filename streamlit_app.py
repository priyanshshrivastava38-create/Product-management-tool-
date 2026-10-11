"""ProductPulse AI — Streamlit entry point.

Run locally:  streamlit run streamlit_app.py
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent))

st.set_page_config(
    page_title="ProductPulse AI",
    page_icon=str(Path(__file__).resolve().parent / "assets" / "icon.svg"),
    layout="wide",
    initial_sidebar_state="expanded",
)


def _bridge_streamlit_secrets() -> None:
    """On Streamlit Community Cloud, secrets live in st.secrets; expose PRODUCTPULSE_* as env vars."""
    try:
        if not st.secrets.load_if_toml_exists():  # no secrets.toml — normal for local runs
            return
        secrets = dict(st.secrets)
    except Exception:
        return
    for key, value in secrets.items():
        if key.startswith("PRODUCTPULSE_") and isinstance(value, str | int | float | bool):
            os.environ.setdefault(key, str(value))


_bridge_streamlit_secrets()

try:
    from productpulse.services import habits
    from productpulse.ui import state, theme
    from productpulse.ui.views import (
        analytics,
        common,
        dashboard,
        data_management,
        explorer,
        insights,
        prioritization,
        roadmap,
        settings_page,
    )
except ImportError as exc:  # friendly message for missing dependencies
    st.error(
        f"A required package is missing: **{exc.name}**. Install dependencies with "
        "`pip install -r requirements.txt` and restart the app."
    )
    st.stop()

theme.setup_page()

try:
    state.db()
except Exception as exc:
    st.error(
        "Could not open the database. Check `PRODUCTPULSE_DATABASE_URL` (see README → Configuration). "
        f"Details: {type(exc).__name__}: {str(exc)[:200]}"
    )
    st.stop()

pages = {
    "dashboard": st.Page(dashboard.render, title="Home", icon=":material/home:", url_path="dashboard", default=True),
    "explorer": st.Page(explorer.render, title="Feedback explorer", icon=":material/search:", url_path="feedback"),
    "insights": st.Page(insights.render, title="Problem themes", icon=":material/lightbulb:", url_path="insights"),
    "prioritization": st.Page(
        prioritization.render, title="Prioritize", icon=":material/leaderboard:", url_path="prioritization"
    ),
    "roadmap": st.Page(roadmap.render, title="Roadmap", icon=":material/map:", url_path="roadmap"),
    "analytics": st.Page(analytics.render, title="Trends", icon=":material/monitoring:", url_path="analytics"),
    "data": st.Page(data_management.render, title="Import & data", icon=":material/upload_file:", url_path="data"),
    "settings": st.Page(settings_page.render, title="How it works", icon=":material/help:", url_path="methodology"),
}
state.PAGES.update(pages)

nav = st.navigation(
    {
        "": [pages["dashboard"]],
        "Discover": [pages["insights"], pages["explorer"], pages["analytics"]],
        "Decide": [pages["prioritization"], pages["roadmap"]],
        "Workspace": [pages["data"], pages["settings"]],
    }
)
theme.sidebar_brand()
_q = st.sidebar.text_input(
    "Search feedback", placeholder="Search all feedback…", key="global_search", label_visibility="collapsed"
)
if _q and _q != st.session_state.get("_global_search_done"):
    st.session_state["_global_search_done"] = _q
    state.go("explorer", explorer_query=_q)
try:
    with state.db().session() as _s:
        _wf = common.workflow_state(_s)
    if not all(_wf.values()):  # onboarding checklist disappears once everything is set up
        theme.sidebar_progress([(label, _wf[key]) for key, label in common.STEPS])
    else:
        with state.db().session() as _s:
            if not habits.get(_s, "celebrated"):
                habits.put(_s, "celebrated", True)
                st.balloons()
                st.toast(
                    "You're all set — feedback in, problems found, prioritised and planned.",
                    icon=":material/celebration:",
                )
except Exception:  # progress is decorative; never block the page
    state.log.warning("sidebar progress unavailable", exc_info=True)
state.show_flash()
nav.run()
