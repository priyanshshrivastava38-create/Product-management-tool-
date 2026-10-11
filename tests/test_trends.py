from __future__ import annotations

from datetime import datetime, timedelta

from productpulse.analysis.trends import TrendParams, compute_trend

END = datetime(2026, 9, 30)
START = END - timedelta(days=200)
P = TrendParams(window_days=28, baseline_windows=3, min_records=5, growth_threshold=1.5)


def days_ago(*ds):
    return [END - timedelta(days=d) for d in ds]


def test_no_dates():
    assert compute_trend([None, None], None, None, P)["status"] == "insufficient_history"


def test_insufficient_history():
    r = compute_trend(days_ago(1, 2, 3, 4, 5, 6), END - timedelta(days=40), END, P)
    assert r["status"] == "insufficient_history"
    assert "112 days are needed" in r["explanation"]


def test_insufficient_volume():
    r = compute_trend(days_ago(1, 2, 50, 60), START, END, P)
    assert r["status"] == "insufficient_volume"


def test_emerging():
    dates = days_ago(*range(1, 13)) + days_ago(40, 70, 100)  # 12 recent vs 1 per baseline window
    r = compute_trend(dates, START, END, P)
    assert r["status"] == "emerging"
    assert r["recent"] == 12 and r["baseline_mean"] == 1.0 and r["growth"] == 12.0


def test_new_theme_with_zero_baseline_is_emerging():
    r = compute_trend(days_ago(*range(1, 9)), START, END, P)
    assert r["status"] == "emerging" and r["growth"] is None


def test_declining_and_stable():
    declining = days_ago(1, 2, 3, 4, 5) + days_ago(*range(30, 110, 3))
    assert compute_trend(declining, START, END, P)["status"] == "declining"
    stable = days_ago(*range(1, 112, 4))
    assert compute_trend(stable, START, END, P)["status"] == "stable"


def test_undated_records_are_counted_separately():
    r = compute_trend([None, *days_ago(1, 2)], START, END, P)
    assert r["undated"] == 1
