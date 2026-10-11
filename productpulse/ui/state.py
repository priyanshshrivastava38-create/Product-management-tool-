"""Shared UI plumbing: cached database, actions with safe error handling, navigation."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from typing import Any

import streamlit as st
from pydantic import ValidationError
from sqlalchemy.orm import Session

from productpulse.analysis.pipeline import AnalysisParams, run_analysis
from productpulse.config import Settings, get_settings
from productpulse.db import Database
from productpulse.db.models import AnalysisRun, utcnow
from productpulse.logging_setup import configure_logging, get_logger

log = get_logger("ui")
PAGES: dict[str, Any] = {}  # filled by streamlit_app.py: key -> st.Page


@st.cache_resource(show_spinner=False)
def _database(url: str) -> Database:
    return Database(url)


def settings() -> Settings:
    s = get_settings()
    configure_logging(s.log_level)
    return s


def db() -> Database:
    return _database(settings().database_url)


def format_validation(exc: ValidationError) -> str:
    parts = []
    for err in exc.errors():
        loc = ".".join(str(x) for x in err["loc"]) or "input"
        parts.append(f"{loc}: {err['msg'].removeprefix('Value error, ')}")
    return "; ".join(parts)


def run_action(fn: Callable[[Session], Any], success: str | None = None, rerun: bool = True) -> Any:
    """Run a mutating operation in its own transaction and report the outcome.

    User-facing validation errors are shown verbatim; unexpected errors are
    logged with a reference ID and shown without internals.
    """
    try:
        with db().session() as session:
            result = fn(session)
    except ValidationError as exc:
        st.error(f"Please check the inputs — {format_validation(exc)}")
        return None
    except ValueError as exc:
        st.error(str(exc))
        return None
    except Exception:
        ref = uuid.uuid4().hex[:8]
        log.exception("action failed", extra={"ctx": {"ref": ref}})
        st.error(f"Something went wrong and nothing was saved (reference {ref}). Check the server logs for details.")
        return None
    if success:
        st.session_state["flash"] = success
    if rerun:
        st.rerun()
    return result


def show_flash() -> None:
    msg = st.session_state.pop("flash", None)
    if msg:
        st.toast(msg, icon=":material/check_circle:")


def go(page: str, **state: Any) -> None:
    for k, v in state.items():
        st.session_state[k] = v
    st.switch_page(PAGES[page])


def analyze(params: AnalysisParams | None = None) -> AnalysisRun | None:
    """Run analysis with a progress bar; record a failed run if it errors."""
    bar = st.progress(0.0, text="Starting analysis…")

    def progress(p: float, msg: str) -> None:
        bar.progress(min(max(p, 0.0), 1.0), text=msg)

    try:
        with db().session() as session:
            run = run_analysis(session, settings(), params, progress)
            run_id = run.id
    except Exception as exc:
        ref = uuid.uuid4().hex[:8]
        log.exception("analysis failed", extra={"ctx": {"ref": ref}})
        with db().session() as session:
            session.add(
                AnalysisRun(
                    status="failed",
                    finished_at=utcnow(),
                    error=f"{type(exc).__name__} (ref {ref})",
                    params=(params or AnalysisParams()).model_dump(),
                )
            )
        bar.empty()
        st.error(f"Analysis failed (reference {ref}). No previous results were changed.")
        return None
    bar.empty()
    with db().session() as session:
        return session.get(AnalysisRun, run_id)
