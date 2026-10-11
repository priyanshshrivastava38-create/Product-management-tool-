from __future__ import annotations

from pathlib import Path

import pandas as pd

from productpulse.analysis.sentiment import classify, word_count

EVAL = Path(__file__).parent / "data" / "sentiment_eval.csv"


def test_basic_labels():
    assert classify("The export is broken and keeps crashing.").label == "negative"
    assert classify("Support was fantastic and super helpful, thank you!").label == "positive"
    assert classify("Please add a dark mode option.").label == "neutral"


def test_score_range_and_reliability():
    r = classify("This product is absolutely wonderful and I love using it every day")
    assert -1 <= r.score <= 1
    assert r.reliability == "high"
    assert classify("ok").reliability == "low"  # too short
    assert classify("").label == "neutral"


def test_mixed_signals_are_low_reliability():
    r = classify("I love the new design but the app crashes constantly and it is awful")
    assert r.reliability == "low"


def test_word_count():
    assert word_count("Don't stop—it's 2026!") == 3


def test_domain_lexicon_accuracy_on_labelled_set():
    df = pd.read_csv(EVAL)
    acc = (df["text"].map(lambda t: classify(t).label) == df["label"]).mean()
    assert acc >= 0.9, acc  # measured 0.94; see docs/AI_EVALUATION.md


def test_known_failure_sarcasm_documented():
    # Sarcasm is a documented limitation: lexicon methods read "Great" as positive.
    assert classify("Great, the dashboard crashed again.").label != "negative"
