"""Deployment health check.

    python scripts/healthcheck.py                 # offline checks (TF-IDF pipeline)
    python scripts/healthcheck.py --with-model    # also load the sentence-transformers model
    python scripts/healthcheck.py --url http://localhost:8501   # also probe a running app

Exit code 0 = all required checks passed. Optional checks report WARN without failing.
"""

from __future__ import annotations

import argparse
import importlib
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

RESULTS: list[tuple[str, str, str]] = []


def check(name: str, required: bool = True):
    def deco(fn):
        def run(*a, **kw):
            t = time.time()
            try:
                detail = fn(*a, **kw) or ""
                RESULTS.append(("PASS", name, f"{detail} ({time.time() - t:.1f}s)"))
            except Exception as exc:
                RESULTS.append(("FAIL" if required else "WARN", name, f"{type(exc).__name__}: {str(exc)[:200]}"))

        return run

    return deco


@check("Python version")
def py_version():
    if sys.version_info < (3, 11):  # noqa: UP036 — runs before deps, reports a clear error
        raise RuntimeError(f"Python {sys.version.split()[0]} found; 3.11+ required")
    return sys.version.split()[0]


@check("Required packages import")
def imports():
    mods = [
        "streamlit",
        "pandas",
        "numpy",
        "sklearn",
        "vaderSentiment",
        "sqlalchemy",
        "plotly",
        "pydantic",
        "pydantic_settings",
        "dotenv",
        "pyarrow",
    ]
    versions = []
    for m in mods:
        mod = importlib.import_module(m)
        versions.append(f"{m} {getattr(mod, '__version__', '')}".strip())
    return ", ".join(versions[:4]) + ", …"


@check("Optional: sentence-transformers importable", required=False)
def st_import():
    import sentence_transformers

    return sentence_transformers.__version__


@check("Configuration loads (no required env vars)")
def config():
    from productpulse.config import get_settings

    s = get_settings()
    return f"db={s.describe()['Database']}, embeddings={s.embedding_backend}, llm={s.describe()['LLM provider']}"


@check("Configured database reachable and schema initialised")
def database():
    from productpulse.config import get_settings
    from productpulse.db import Database
    from productpulse.services import workspace

    db = Database(get_settings().database_url)
    with db.session() as s:
        empty = workspace.is_empty(s)
    return "empty workspace" if empty else "workspace has data"


@check("Bundled sample data present")
def samples():
    from productpulse.config import SAMPLE_DIR

    names = ["sample_feedback.csv", "synthetic_feedback.csv", "messy_feedback_example.csv"]
    missing = [n for n in names if not (SAMPLE_DIR / n).exists()]
    if missing:
        raise FileNotFoundError(", ".join(missing))
    return ", ".join(names)


@check("Pipeline smoke test (import → analyse → prioritise → brief, TF-IDF, temp DB)")
def pipeline():
    from productpulse.analysis.pipeline import run_analysis
    from productpulse.config import Settings
    from productpulse.db import Database
    from productpulse.exports.briefs import product_brief
    from productpulse.services import prioritization, roadmap, themes, workspace

    with tempfile.TemporaryDirectory() as d:
        settings = Settings(database_url=f"sqlite:///{d}/hc.db", embedding_backend="tfidf")
        db = Database(settings.database_url)
        with db.session() as s:
            n = workspace.load_sample(s, settings).inserted
            run = run_analysis(s, settings)
        with db.session() as s:
            t = themes.active_themes(s)[0]
            data, ev = prioritization.suggest_from_theme(s, t, "Moderate")
            opp = prioritization.create_opportunity(s, data, ev, t.id)
            roadmap.add_to_roadmap(s, [opp.id], "Now")
            md = product_brief(s, opp)
        db.engine.dispose()
    if "## 9. Open questions" not in md:
        raise RuntimeError("brief incomplete")
    return f"{n} records, {run.n_themes} themes"


def model_check():
    @check("Optional: embedding model loads and encodes", required=False)
    def _m():
        from productpulse.analysis.embeddings import load_sentence_model
        from productpulse.config import get_settings

        m = load_sentence_model(get_settings().embedding_model)
        v = m.encode(["export fails"], normalize_embeddings=True)
        return f"dim={v.shape[1]}"

    _m()


def url_check(url: str):
    @check(f"Running app responds at {url}")
    def _u():
        import httpx

        r = httpx.get(url.rstrip("/") + "/_stcore/health", timeout=10)
        r.raise_for_status()
        return r.text.strip()

    _u()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--with-model", action="store_true")
    ap.add_argument("--url")
    a = ap.parse_args()
    for fn in (py_version, imports, st_import, config, database, samples, pipeline):
        fn()
    if a.with_model:
        model_check()
    if a.url:
        url_check(a.url)
    width = max(len(n) for _, n, _ in RESULTS)
    for status, name, detail in RESULTS:
        print(f"[{status}] {name.ljust(width)}  {detail}")
    failed = [r for r in RESULTS if r[0] == "FAIL"]
    print("\nHealth check " + ("FAILED" if failed else "passed") + ".")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
