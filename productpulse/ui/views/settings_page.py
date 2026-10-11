"""Settings and Methodology — configuration, how every number is produced, limitations, privacy."""

from __future__ import annotations

import importlib.util

import pandas as pd
import streamlit as st

from productpulse import __version__
from productpulse.services import themes
from productpulse.ui import state, theme

METHODOLOGY = """
#### Sentiment
VADER (lexicon + rules) assigns each record a **compound score** in −1…1. Labels: positive ≥ 0.05,
negative ≤ −0.05, otherwise neutral. The score is **not a probability**. The *reliability* flag is a
heuristic: **low** for < 4 words, mixed positive/negative signals or |score| < 0.15; **high** for ≥ 6 words
and |score| ≥ 0.5; otherwise **medium**.
*Limitations:* sarcasm (“great, it crashed again”), domain terms (“kill the process”, “429”), negation
over long spans, non-English text and very short answers are often misread.

#### Themes (clustering)
Records with at least 3 words are embedded with a local Sentence-Transformers model
(`all-MiniLM-L6-v2` by default) or, if unavailable, TF-IDF + truncated SVD (LSA). Vectors are clustered with
average-linkage agglomerative clustering on cosine distance (HDBSCAN only above 6,000 records). Groups smaller
than the minimum theme size stay **unclustered** rather than being forced into a theme.

#### Labels
Keywords come from class-based TF-IDF over each theme's text; the label joins the most distinctive keywords.
Problem statements are a fixed template filled with computed counts. Users can rename, merge and split themes;
manual names carry over to the next run when ≥ 50% of members match (Jaccard).

#### Duplicates and similar requests
Exact duplicates share the same normalised text. Semantic near-duplicates are pairs with cosine similarity at or
above the threshold (top 5 neighbours per record). Pairs are suggestions to review — records are never merged.

#### Emerging problems
Reference date = latest feedback date. A theme is **emerging** when the last *window* holds at least the minimum
number of records and is ≥ the growth threshold × the average of the preceding *baseline* windows (and at least
3 records more). If the data does not cover baseline + comparison windows, the status is *not enough history*.

#### Evidence quality (0–6 points)
Volume (≥ 8 → 1, ≥ 20 → 2) · distinct customers (≥ 4 → 1, ≥ 10 → 2) · ≥ 3 sources → 1 · cohesion above
bar → 1. **Strong** ≥ 5, **Moderate** 3–4, **Limited** ≤ 2. A transparent rubric, not statistical confidence.

#### RICE
(Reach × Impact × Confidence) ÷ Effort. Impact 0.25/0.5/1/2/3; confidence 10–100%; effort > 0 person-months.
Reach suggested from a theme is a **proxy** (distinct customers in the evidence) and is labelled as such.

#### Optional AI summaries
Only if an LLM provider is configured. The model receives only the selected theme's records (ID + text) and must
return JSON citing record IDs. Output is rejected — and the deterministic summary kept — if it is malformed,
cites IDs outside the theme, or quotes text that does not appear verbatim in the evidence.
"""

PRIVACY = """
- **Stored:** uploaded feedback rows (text and mapped metadata), derived analysis (sentiment, themes, similarity
  pairs, embedding vectors), opportunities, scenarios, roadmap items/versions and an audit log — all in the
  database configured by `PRODUCTPULSE_DATABASE_URL` (local SQLite file by default).
- **Not stored:** the uploaded file itself (only its SHA-256 hash and file name), API keys (read from the environment).
- **External services:** none by default. The embedding model is downloaded once from Hugging Face and runs
  locally; feedback text is not sent there. If you configure an LLM provider, the text of the records in a theme
  is sent to that provider **only when you click “Generate AI summary”**.
- **Deletion:** delete an import (and its records) or reset the whole workspace from Data Management.
- This is a portfolio/demo application. It has not been security-audited and makes no compliance claims.
"""


def render() -> None:
    st.session_state["_page_key"] = "settings"
    theme.header(
        "How it works",
        "How ProductPulse calculates everything it shows you, what it stores, and its limits.",
        "Workspace",
        "help",
    )
    settings = state.settings()
    tabs = st.tabs(["Configuration", "Methodology", "Privacy & limitations"])
    with tabs[0]:
        st.dataframe(
            pd.DataFrame(list(settings.describe().items()), columns=["Setting", "Value"]),
            hide_index=True,
            use_container_width=True,
        )
        st.caption(
            "Configure with environment variables (`PRODUCTPULSE_*`, see `.env.example`) or Streamlit secrets. "
            "Secrets are never displayed."
        )
        st_ok = importlib.util.find_spec("sentence_transformers") is not None
        st.markdown(f"- Sentence-Transformers installed: **{'yes' if st_ok else 'no — TF-IDF fallback will be used'}**")
        with state.db().session() as s:
            run = themes.latest_run(s)
            if run:
                st.markdown(
                    f"- Latest analysis used: **{run.embedding_method}** + **{run.clustering_method}** "
                    f"(run #{run.id})"
                )
        if settings.is_sqlite:
            st.warning(
                "Storage is a local SQLite file. On hosts with ephemeral disks (e.g. Streamlit Community "
                "Cloud) data is lost on restart or redeploy — configure PostgreSQL for durable storage "
                "(docs/DEPLOYMENT.md)."
            )
        st.caption(f"ProductPulse AI v{__version__}")
    with tabs[1]:
        st.markdown(METHODOLOGY)
        st.markdown(
            "**Labels used across the app:** "
            + " ".join(theme.badge(k) for k in ("observed", "calculated", "assumption", "ai")),
            unsafe_allow_html=True,
        )
        st.caption(
            "Observed = verbatim stored feedback · Calculated = deterministic computation · "
            "Assumption = team estimate · AI-generated = validated model interpretation."
        )
    with tabs[2]:
        st.markdown(PRIVACY)
        st.markdown("""
**Known limitations**
- Sentiment is lexicon-based and English-centric.
- Clustering is unsupervised; themes may mix related problems or split one problem in two — curate them.
- The TF-IDF fallback groups by shared words and is markedly weaker than the semantic model on paraphrases.
- Trend detection needs dated records covering several windows; small datasets will show “not enough history”.
- Reach suggested from feedback is a proxy; vocal customers are over-represented.
- Analysis runs synchronously in the web process; very large datasets are capped by `PRODUCTPULSE_MAX_ANALYSIS_ROWS`.
""")
