from __future__ import annotations

import json

import httpx
import pytest

from productpulse.analysis.pipeline import run_analysis
from productpulse.db.models import Theme
from productpulse.llm import provider
from productpulse.services import themes

EVIDENCE = [("fb_1", "CSV export times out on large reports"), ("fb_2", "Exporting big reports to CSV fails")]


def enabled(settings):
    return settings.model_copy(update={"llm_provider": "anthropic", "llm_api_key": "sk-secret-123"})


def ok_caller(_s, _sys, _user):
    return json.dumps(
        {
            "summary": "Customers report that CSV exports of large reports time out or fail.",
            "cited_ids": ["fb_1", "fb_2"],
        }
    )


def test_disabled_uses_deterministic(settings):
    out = provider.summarize_theme(settings, "Export", EVIDENCE, "deterministic text")
    assert out.used_fallback and out.summary == "deterministic text" and out.provider == "deterministic"


def test_valid_output_is_accepted(settings):
    out = provider.summarize_theme(enabled(settings), "Export", EVIDENCE, "det", caller=ok_caller)
    assert not out.used_fallback
    assert out.cited_ids == ["fb_1", "fb_2"]
    assert out.provider == "anthropic:claude-opus-5-5"


def test_code_fenced_json_is_accepted(settings):
    body = json.dumps({"summary": "Large CSV exports fail for several customers.", "cited_ids": ["fb_1"]})

    def fenced(*_):
        return "```json\n" + body + "\n```"

    assert not provider.summarize_theme(enabled(settings), "E", EVIDENCE, "det", caller=fenced).used_fallback


@pytest.mark.parametrize(
    ("raw", "reason"),
    [
        ("not json at all", "not a JSON object"),
        (json.dumps({"summary": "too short", "cited_ids": ["fb_1"]}), "schema"),
        (json.dumps({"summary": "Customers report failing CSV exports of reports.", "cited_ids": []}), "schema"),
        (
            json.dumps({"summary": "Customers report failing CSV exports of reports.", "cited_ids": ["fb_999"]}),
            "not in the supplied evidence",
        ),
        (
            json.dumps(
                {
                    "summary": 'One customer said "the export deleted all my data" which is alarming.',
                    "cited_ids": ["fb_1"],
                }
            ),
            "quotation",
        ),
    ],
)
def test_invalid_output_falls_back(settings, raw, reason):
    out = provider.summarize_theme(enabled(settings), "E", EVIDENCE, "det", caller=lambda *_: raw)
    assert out.used_fallback and out.summary == "det"
    assert reason in out.reason


def test_verbatim_quote_is_allowed(settings):
    raw = json.dumps(
        {"summary": 'Customers say "CSV export times out on large reports" repeatedly.', "cited_ids": ["fb_1"]}
    )
    assert not provider.summarize_theme(enabled(settings), "E", EVIDENCE, "d", caller=lambda *_: raw).used_fallback


def test_network_errors_and_timeouts_fall_back_without_leaking_key(settings):
    def timeout(*_):
        raise httpx.ReadTimeout("timed out talking to sk-secret-123")

    out = provider.summarize_theme(enabled(settings), "E", EVIDENCE, "det", caller=timeout)
    assert out.used_fallback and "ReadTimeout" in out.reason
    assert "sk-secret-123" not in out.reason


def test_openai_compatible_caller_with_mock_transport(settings, monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == "Bearer sk-secret-123"
        return httpx.Response(500, json={"error": "boom"})

    real_post = httpx.post

    def fake_post(url, **kw):
        with httpx.Client(transport=httpx.MockTransport(handler)) as c:
            return c.post(url, **kw)

    monkeypatch.setattr(httpx, "post", fake_post)
    s = settings.model_copy(update={"llm_provider": "openai", "llm_api_key": "sk-secret-123"})
    out = provider.summarize_theme(s, "E", EVIDENCE, "det")
    assert out.used_fallback and "HTTPStatusError" in out.reason
    monkeypatch.setattr(httpx, "post", real_post)


def test_generate_ai_summary_stores_only_validated_output(loaded_db, settings):
    with loaded_db.session() as s:
        run_analysis(s, settings)
    with loaded_db.session() as s:
        t = themes.active_themes(s)[0]
        ids = [m.feedback_id for m in t.memberships][:2]
        tid = t.id
        bad = themes.generate_ai_summary(s, enabled(settings), tid, caller=lambda *_: "garbage")
        assert bad.used_fallback and s.get(Theme, tid).ai_summary is None
        good_raw = json.dumps({"summary": "Customers describe a recurring problem in this theme.", "cited_ids": ids})
        good = themes.generate_ai_summary(s, enabled(settings), tid, caller=lambda *_: good_raw)
        assert not good.used_fallback
    with loaded_db.session() as s:
        t = s.get(Theme, tid)
        assert t.ai_summary.startswith("Customers describe") and t.ai_cited_ids == ids
