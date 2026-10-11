"""Lexicon-based sentiment with VADER.

The VADER *compound* score is a normalised lexicon sum in [-1, 1]. It is NOT a
probability and is never presented as one. The *reliability* flag is a
transparent heuristic (see docs/PRODUCT_METRICS.md):

- low:    fewer than 4 words, or positive AND negative signals (each >= 10% of tokens),
          or |compound| < 0.15 (weak signal)
- high:   at least 6 words and |compound| >= 0.5 without mixed signals
- medium: everything else
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache

from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer

POSITIVE_THRESHOLD = 0.05
NEGATIVE_THRESHOLD = -0.05
_WORD = re.compile(r"[^\W\d_]+(?:'[^\W\d_]+)?")


@dataclass(frozen=True)
class SentimentResult:
    label: str  # positive | neutral | negative
    score: float  # VADER compound, -1..1
    reliability: str  # low | medium | high


# Domain adjustments for product feedback (VADER valence scale −4…+4). VADER's general lexicon
# misses software-failure vocabulary and scores polite request words ("please", "support",
# "help", "like") as positive, which turns complaints and feature requests positive.
# Measured effect on tests/data/sentiment_eval.csv is recorded in docs/AI_EVALUATION.md.
DOMAIN_LEXICON: dict[str, float] = {
    **dict.fromkeys(["crash", "crashes", "crashed", "crashing", "unusable", "corrupted", "nightmare"], -2.5),
    **dict.fromkeys(
        [
            "broken",
            "breaks",
            "buggy",
            "freezes",
            "freeze",
            "frozen",
            "fails",
            "failed",
            "failing",
            "unreliable",
            "hangs",
            "laggy",
            "lost",
            "throttling",
        ],
        -2.0,
    ),
    **dict.fromkeys(
        [
            "bug",
            "bugs",
            "error",
            "errors",
            "timeout",
            "timeouts",
            "slow",
            "slowly",
            "confusing",
            "cluttered",
            "irrelevant",
            "glitch",
            "glitchy",
            "flooded",
        ],
        -1.6,
    ),
    **dict.fromkeys(["please", "support", "help", "like", "nice", "want", "allow"], 0.0),
    "faster": 1.5,
    "flawlessly": 2.5,
    "responsive": 1.5,
}


@lru_cache(maxsize=1)
def _analyzer() -> SentimentIntensityAnalyzer:
    analyzer = SentimentIntensityAnalyzer()
    analyzer.lexicon.update(DOMAIN_LEXICON)
    return analyzer


def word_count(text: str) -> int:
    return len(_WORD.findall(text))


def classify(text: str) -> SentimentResult:
    scores = _analyzer().polarity_scores(text or "")
    compound = round(float(scores["compound"]), 4)
    if compound >= POSITIVE_THRESHOLD:
        label = "positive"
    elif compound <= NEGATIVE_THRESHOLD:
        label = "negative"
    else:
        label = "neutral"

    words = word_count(text or "")
    mixed = scores["pos"] >= 0.1 and scores["neg"] >= 0.1
    if words < 4 or mixed or abs(compound) < 0.15:
        reliability = "low"
    elif words >= 6 and abs(compound) >= 0.5:
        reliability = "high"
    else:
        reliability = "medium"
    return SentimentResult(label, compound, reliability)


def classify_many(texts: list[str]) -> list[SentimentResult]:
    return [classify(t) for t in texts]
