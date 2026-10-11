"""Reproduce the measurements reported in docs/AI_EVALUATION.md.

    python scripts/evaluate.py            # TF-IDF + semantic model (downloads ~90 MB once)
    python scripts/evaluate.py --no-model # TF-IDF only

Datasets: data/sample/synthetic_feedback.csv with ground-truth themes in
synthetic_ground_truth.csv (never imported by the app), and the hand-labelled
tests/data/sentiment_eval.csv. Both were written by the project author, so the
numbers measure behaviour on controlled data, not real-world accuracy.
"""

from __future__ import annotations

import argparse
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import pandas as pd  # noqa: E402
from sklearn.metrics import adjusted_rand_score, normalized_mutual_info_score  # noqa: E402
from sqlalchemy import select  # noqa: E402
from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer  # noqa: E402

from productpulse.analysis.pipeline import AnalysisParams, run_analysis  # noqa: E402
from productpulse.analysis.sentiment import classify  # noqa: E402
from productpulse.config import SAMPLE_DIR, Settings  # noqa: E402
from productpulse.db import Database  # noqa: E402
from productpulse.db.models import Feedback, SimilarityPair  # noqa: E402
from productpulse.services import themes, workspace  # noqa: E402


def sentiment_eval() -> None:
    df = pd.read_csv(ROOT / "tests" / "data" / "sentiment_eval.csv")
    plain = SentimentIntensityAnalyzer()

    def lab(c: float) -> str:
        return "positive" if c >= 0.05 else "negative" if c <= -0.05 else "neutral"

    df["plain"] = [lab(plain.polarity_scores(t)["compound"]) for t in df.text]
    df["domain"] = [classify(t).label for t in df.text]
    print(f"\n## Sentiment (n={len(df)})")
    for col in ("plain", "domain"):
        acc = (df[col] == df.label).mean()
        per = {
            c: round(float(((df[col] == c) & (df.label == c)).sum() / max((df.label == c).sum(), 1)), 2)
            for c in ("negative", "neutral", "positive")
        }
        print(f"{col:7s} accuracy={acc:.2f}  recall by class={per}")
    print("domain-lexicon errors:")
    for _, r in df[df.domain != df.label].iterrows():
        print(f"  - [{r.label} → {r.domain}] {r.text}")


def clustering_eval(backend: str, threshold: float | None = None, algorithm: str = "auto") -> None:
    truth = pd.read_csv(SAMPLE_DIR / "synthetic_ground_truth.csv")
    with tempfile.TemporaryDirectory() as d:
        settings = Settings(database_url=f"sqlite:///{d}/eval.db", embedding_backend=backend)
        db = Database(settings.database_url)
        with db.session() as s:
            workspace.load_sample(s, settings)
            t0 = time.time()
            run = run_analysis(s, settings, AnalysisParams(distance_threshold=threshold, algorithm=algorithm))
            secs = time.time() - t0
        with db.session() as s:
            ext = {f.id: f.external_id for f in s.scalars(select(Feedback))}
            pred: dict[str, int] = {}
            for t in themes.active_themes(s):
                for m in t.memberships:
                    pred[ext[m.feedback_id]] = t.id
            gt = dict(zip(truth.feedback_id, truth.theme, strict=True))
            pairs = list(s.scalars(select(SimilarityPair).where(SimilarityPair.kind == "semantic")))
            same = sum(1 for p in pairs if gt.get(ext[p.feedback_a_id]) == gt.get(ext[p.feedback_b_id]))
            # Evidence traceability: every quote is a stored record, verbatim
            ok = all(themes.verify_quotes(s, themes.representative_members(t)) for t in themes.active_themes(s))
        db.engine.dispose()
    tt = truth[truth.theme != "noise"].copy()
    tt["pred"] = tt.feedback_id.map(lambda x: pred.get(x, -1))
    noise = truth[truth.theme == "noise"].feedback_id
    print(
        f"\n## Clustering — {run.embedding_method} + {run.clustering_method} "
        f"(threshold {run.params['distance_threshold_used']})"
    )
    print(
        f"themes={run.n_themes} (13 generated) unclustered={run.n_unclustered} excluded_short={run.n_excluded} "
        f"time={secs:.1f}s"
    )
    print(
        f"ARI={adjusted_rand_score(tt.theme, tt.pred):.3f} NMI={normalized_mutual_info_score(tt.theme, tt.pred):.3f} "
        f"coverage={(tt.pred >= 0).mean():.3f} noise_kept_out={(~noise.isin(pred)).mean():.3f}"
    )
    prec = same / len(pairs) if pairs else float("nan")
    print(f"semantic near-duplicate pairs={len(pairs)} same-ground-truth-theme precision={prec:.3f}")
    print(f"quotes verbatim & traceable: {ok}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-model", action="store_true")
    a = ap.parse_args()
    sentiment_eval()
    clustering_eval("tfidf")
    if not a.no_model:
        for thr in (0.55, 0.65, 0.75):
            clustering_eval("sentence-transformers", thr)
        clustering_eval("sentence-transformers", None, "hdbscan")


if __name__ == "__main__":
    main()
