"""Time-aware emerging-problem detection.

Reference point: the latest feedback date in the workspace (not "today"), so
historical datasets are analysed on their own timeline.

- Comparison window: the last ``window_days`` up to the reference date.
- Baseline: the mean count over the ``baseline_windows`` windows of equal
  length immediately before it.

A theme is *emerging* only if:
1. the dataset covers the full baseline + comparison span (else "insufficient history"),
2. the comparison window has at least ``min_records`` records (else "insufficient volume"),
3. recent / baseline >= ``growth_threshold`` and the absolute increase is >= 3 records.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from pydantic import BaseModel, Field


class TrendParams(BaseModel):
    window_days: int = Field(default=28, ge=7, le=180)
    baseline_windows: int = Field(default=3, ge=1, le=12)
    min_records: int = Field(default=5, ge=2, le=1000)
    growth_threshold: float = Field(default=1.5, ge=1.1, le=10)


def dataset_span(dates: list[datetime]) -> tuple[datetime | None, datetime | None]:
    return (min(dates), max(dates)) if dates else (None, None)


def compute_trend(
    theme_dates: list[datetime | None],
    data_start: datetime | None,
    reference_end: datetime | None,
    params: TrendParams,
) -> dict:
    dated = [d for d in theme_dates if d is not None]
    out: dict = {
        "status": "insufficient_history",
        "recent": 0,
        "baseline_mean": None,
        "growth": None,
        "window_days": params.window_days,
        "baseline_windows": params.baseline_windows,
        "undated": len(theme_dates) - len(dated),
    }
    if reference_end is None or data_start is None:
        out["explanation"] = "No dated feedback, so trends cannot be computed."
        return out

    w = timedelta(days=params.window_days)
    recent_start = reference_end - w
    baseline_start = recent_start - w * params.baseline_windows
    out.update(
        reference_end=reference_end.date().isoformat(),
        recent_start=recent_start.date().isoformat(),
        baseline_start=baseline_start.date().isoformat(),
    )
    recent = sum(1 for d in dated if recent_start < d <= reference_end)
    baseline_total = sum(1 for d in dated if baseline_start < d <= recent_start)
    baseline_mean = baseline_total / params.baseline_windows
    out.update(recent=recent, baseline_mean=round(baseline_mean, 2))

    if data_start > baseline_start + timedelta(days=1):
        span = (reference_end - data_start).days
        need = params.window_days * (params.baseline_windows + 1)
        out["explanation"] = (
            f"Data covers {span} days; {need} days are needed "
            f"({params.baseline_windows} baseline window(s) + 1 comparison window of {params.window_days} days)."
        )
        return out
    if recent < params.min_records:
        out["status"] = "insufficient_volume"
        out["explanation"] = (
            f"{recent} record(s) in the last {params.window_days} days; at least {params.min_records} "
            "are required before calling a trend."
        )
        return out

    growth = recent / baseline_mean if baseline_mean > 0 else None
    out["growth"] = round(growth, 2) if growth is not None else None
    increase = recent - baseline_mean
    if (growth is None or growth >= params.growth_threshold) and increase >= 3:
        out["status"] = "emerging"
        base_txt = f"{baseline_mean:.1f}" if baseline_mean else "0"
        out["explanation"] = (
            f"{recent} records in the last {params.window_days} days vs a baseline average of "
            f"{base_txt} per {params.window_days}-day window"
            + (f" ({growth:.1f}× growth)." if growth else " (new theme).")
        )
    elif growth is not None and growth <= 1 / params.growth_threshold:
        out["status"] = "declining"
        out["explanation"] = f"{recent} recent records vs a baseline average of {baseline_mean:.1f} ({growth:.1f}×)."
    else:
        out["status"] = "stable"
        out["explanation"] = f"{recent} recent records vs a baseline average of {baseline_mean:.1f}."
    return out
