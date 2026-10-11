from __future__ import annotations

import pytest
from sqlalchemy import func, select

from productpulse.analysis.pipeline import AnalysisParams, run_analysis
from productpulse.db.models import AnalysisRun, Feedback, SimilarityPair, Theme, ThemeMembership
from productpulse.services import themes


def _run(db, settings, **kw):
    with db.session() as s:
        return run_analysis(s, settings, AnalysisParams(**kw)).id


def test_analysis_produces_themes_sentiment_and_pairs(loaded_db, settings):
    run_id = _run(loaded_db, settings)
    with loaded_db.session() as s:
        run = s.get(AnalysisRun, run_id)
        assert run.status == "completed"
        assert run.embedding_method == "tfidf-lsa"
        assert run.params["distance_threshold_used"] == 0.95
        assert run.n_themes >= 3
        assert run.n_excluded > 0  # short noise records like "ok"
        assert s.scalar(select(func.count()).select_from(Feedback).where(Feedback.sentiment_label.is_(None))) == 0
        assert run.n_similar_pairs == s.scalar(select(func.count()).select_from(SimilarityPair))
        active = themes.active_themes(s)
        assert len(active) == run.n_themes
        assert all(t.keywords and t.label and t.problem_statement for t in active)
        # Short records are never forced into a theme
        short = list(s.scalars(select(Feedback).where(Feedback.analyzable.is_(False))))
        assert short
        member_ids = set(s.scalars(select(ThemeMembership.feedback_id)))
        assert not member_ids & {f.id for f in short}


def test_semantic_path_with_mocked_model(loaded_db, settings, fake_model):
    run_id = _run(loaded_db, settings, embedding_backend="sentence-transformers")
    with loaded_db.session() as s:
        run = s.get(AnalysisRun, run_id)
        assert run.embedding_method.startswith("sentence-transformers")
        assert run.params["distance_threshold_used"] == 0.65


def test_empty_workspace_analysis_is_graceful(db, settings):
    run_id = _run(db, settings)
    with db.session() as s:
        run = s.get(AnalysisRun, run_id)
        assert run.status == "completed" and run.n_themes == 0
        assert "needed to form themes" in run.notes


def test_evidence_is_traceable_to_stored_records(loaded_db, settings):
    _run(loaded_db, settings)
    with loaded_db.session() as s:
        for t in themes.active_themes(s):
            ins = themes.insight(s, t)
            assert ins["n"] == len(t.memberships) > 0
            assert themes.verify_quotes(s, ins["quotes"])
            for q in ins["quotes"]:
                stored = s.get(Feedback, q.id)
                assert stored.text_original == q.text_original  # verbatim
                assert q.id in {m.feedback_id for m in t.memberships}
            assert ins["quality"].level in {"Strong", "Moderate", "Limited"}
            assert sum(ins["sentiment"].values()) == ins["n"]


def test_rename_merge_split_remove(loaded_db, settings):
    _run(loaded_db, settings)
    with loaded_db.session() as s:
        a, b, c = [t.id for t in themes.active_themes(s)[:3]]
    with loaded_db.session() as s:
        themes.rename(s, a, "  Export   reliability ")
    with loaded_db.session() as s:
        assert s.get(Theme, a).label == "Export reliability"
        size_a, size_b = len(s.get(Theme, a).memberships), len(s.get(Theme, b).memberships)
        themes.merge(s, a, [b])
    with loaded_db.session() as s:
        ta = s.get(Theme, a)
        assert len(ta.memberships) == size_a + size_b
        assert s.get(Theme, b).is_active is False and s.get(Theme, b).merged_into_id == a
        move = [m.feedback_id for m in ta.memberships][:3]
        new = themes.split(s, a, move, "Split part")
        new_id = new.id
    with loaded_db.session() as s:
        assert len(s.get(Theme, new_id).memberships) == 3
        assert not {m.feedback_id for m in s.get(Theme, a).memberships} & set(move)
        before = len(s.get(Theme, c).memberships)
        drop = [s.get(Theme, c).memberships[0].feedback_id]
        themes.remove_members(s, c, drop)
    with loaded_db.session() as s:
        assert len(s.get(Theme, c).memberships) == before - 1
        assert s.get(Feedback, drop[0]) is not None  # record itself is never deleted


def test_curation_validation(loaded_db, settings):
    _run(loaded_db, settings)
    with loaded_db.session() as s:
        t = themes.active_themes(s)[0]
        with pytest.raises(themes.ThemeError):
            themes.rename(s, t.id, "x")
        with pytest.raises(themes.ThemeError):
            themes.merge(s, t.id, [t.id])
        with pytest.raises(themes.ThemeError):
            themes.split(s, t.id, [m.feedback_id for m in t.memberships], "all")
        with pytest.raises(themes.ThemeError):
            themes.split(s, t.id, ["fb_not_a_member"], "x")


def test_rerun_carries_manual_names_and_keeps_history(loaded_db, settings):
    _run(loaded_db, settings)
    with loaded_db.session() as s:
        t = themes.active_themes(s)[0]
        themes.rename(s, t.id, "Customer-named theme")
    second = _run(loaded_db, settings)
    with loaded_db.session() as s:
        labels = [x.label for x in themes.active_themes(s)]
        assert "Customer-named theme" in labels
        assert all(x.run_id == second for x in themes.active_themes(s))
        assert s.scalar(select(func.count()).select_from(AnalysisRun)) == 2
        assert s.scalar(select(func.count()).select_from(Theme).where(Theme.is_active.is_(False))) > 0
        assert "Carried over 1" in s.get(AnalysisRun, second).notes


def test_review_pair_never_deletes(loaded_db, settings):
    _run(loaded_db, settings, similarity_threshold=0.8)
    with loaded_db.session() as s:
        pair = s.scalars(select(SimilarityPair)).first()
        n = s.scalar(select(func.count()).select_from(Feedback))
        themes.review_pair(s, pair.id, "confirmed")
    with loaded_db.session() as s:
        assert s.get(SimilarityPair, pair.id).review_status == "confirmed"
        assert s.scalar(select(func.count()).select_from(Feedback)) == n
        with pytest.raises(themes.ThemeError):
            themes.review_pair(s, pair.id, "merged")


def test_similar_pairs_are_ordered_original_first(loaded_db, settings):
    _run(loaded_db, settings, similarity_threshold=0.8)
    with loaded_db.session() as s:
        for p in s.scalars(select(SimilarityPair).limit(50)):
            a, b = s.get(Feedback, p.feedback_a_id), s.get(Feedback, p.feedback_b_id)
            if a.created_at and b.created_at:
                assert a.created_at <= b.created_at
