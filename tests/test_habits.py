from __future__ import annotations

from conftest import SMALL_SAMPLE
from sqlalchemy import update

from productpulse.analysis.pipeline import run_analysis
from productpulse.db.models import AppState, Feedback
from productpulse.ingestion.csv_import import import_csv, read_csv_bytes, suggest_mapping
from productpulse.services import habits, themes


def test_key_value_round_trip(db):
    with db.session() as s:
        assert habits.get(s, "x", 5) == 5
        habits.put(s, "x", {"a": 1})
    with db.session() as s:
        assert habits.get(s, "x") == {"a": 1}
        habits.put(s, "x", [1, 2])
    with db.session() as s:
        assert habits.get(s, "x") == [1, 2]
        assert s.get(AppState, "x") is not None


def test_last_visit_and_new_feedback(loaded_db, settings):
    with loaded_db.session() as s:
        assert habits.touch_visit(s) is None  # first visit
    with loaded_db.session() as s:
        prev = habits.touch_visit(s)
        assert prev is not None
        assert habits.since(s, prev)["new_feedback"] == 0
        s.execute(update(Feedback).values(imported_at=prev.replace(year=prev.year + 1)))
        assert habits.since(s, prev)["new_feedback"] > 100


def test_since_compares_last_two_runs(loaded_db, settings):
    with loaded_db.session() as s:
        run_analysis(s, settings)
    with loaded_db.session() as s:
        assert "compared_runs" not in habits.since(s, None)
        run_analysis(s, settings)
    with loaded_db.session() as s:
        d = habits.since(s, None)
        assert d["compared_runs"][0] < d["compared_runs"][1]
        assert d["new_themes"] == [] and d["grown"] == []  # identical reruns: nothing changed


def test_watchlist_survives_reanalysis(loaded_db, settings):
    with loaded_db.session() as s:
        run_analysis(s, settings)
    with loaded_db.session() as s:
        t = themes.active_themes(s)[0]
        habits.watch(s, t.id)
        label, old_id = t.label, t.id
        assert habits.is_watched(s, t)
    with loaded_db.session() as s:
        run_analysis(s, settings)  # new theme IDs
    with loaded_db.session() as s:
        w = habits.watched(s)
        assert len(w) == 1 and w[0]["theme"] is not None
        assert w[0]["theme"].id != old_id and w[0]["theme"].label == label
        habits.unwatch(s, label)
        assert habits.watched(s) == []


def test_remembered_mapping(db, settings):
    data = b"Comment,When,Channel\nExport is broken,2026-09-01,Support\n"
    parsed = read_csv_bytes(data, "x.csv", settings)
    with db.session() as s:
        assert habits.remembered_mapping(s, parsed.headers) is None
        m = suggest_mapping(parsed.headers)
        habits.remember_mapping(s, parsed.headers, m)
    with db.session() as s:
        assert habits.remembered_mapping(s, parsed.headers) == m
        assert habits.remembered_mapping(s, ["other"]) is None


def test_weekly_summary_mentions_real_numbers(db, settings):
    with db.session() as s:
        import_csv(s, SMALL_SAMPLE.read_bytes(), SMALL_SAMPLE.name, settings)
        run_analysis(s, settings)
    with db.session() as s:
        t = themes.active_themes(s)[0]
        habits.watch(s, t.id)
        text = habits.weekly_summary(s)
        assert "new pieces of feedback this week" in text
        assert t.label in text and "Watching" in text


def test_since_matches_themes_by_content_not_label(loaded_db, settings):
    with loaded_db.session() as s:
        run_analysis(s, settings)
    with loaded_db.session() as s:
        t = themes.active_themes(s)[0]
        themes.rename(s, t.id, "Completely different name")
        run_analysis(s, settings)
    with loaded_db.session() as s:
        d = habits.since(s, None)
        assert d["new_themes"] == []  # same feedback groups → not reported as new despite label changes
