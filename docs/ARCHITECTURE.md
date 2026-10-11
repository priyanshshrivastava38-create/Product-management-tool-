# Architecture

## Overview

ProductPulse AI is a single Python process: Streamlit serves the UI and calls a plain-Python service layer, which persists to a relational database through SQLAlchemy. There are no queues, caches or containers to operate.

```
 Browser ──► Streamlit (streamlit_app.py → productpulse/ui/views/*)
                 │  UI only: widgets, charts, navigation; mutations go through ui/state.run_action()
                 ▼
            Services (productpulse/services/*)           Exports (productpulse/exports)
              feedback search · themes · prioritization · roadmap · analytics · audit · workspace
                 │                         │
                 ▼                         ▼
            Ingestion                 Analysis pipeline (productpulse/analysis)
            (csv_import)              sentiment → embeddings → clustering → labels → similarity → trends
                 │                         │                └── optional LLM summaries (llm/provider.py)
                 ▼                         ▼
            SQLAlchemy ORM (productpulse/db) ──► SQLite (default) or PostgreSQL
```

### Layers and responsibilities

| Layer | Modules | Rules |
|---|---|---|
| UI | `streamlit_app.py`, `ui/state.py`, `ui/theme.py`, `ui/views/*` | No SQL or business rules. Reads happen inside one session per rerun. Every write goes through `run_action()` in its own transaction, which shows validation messages verbatim and unexpected errors as a reference ID only. |
| Services | `services/*` | Deterministic business logic, validation (Pydantic) and audit events. Callable from tests and scripts without Streamlit. |
| Analysis | `analysis/*` | Pure functions over text and vectors plus the `run_analysis` orchestrator. |
| Ingestion | `ingestion/csv_import.py` | Parse, map, validate and import. Treats files as data only. |
| LLM | `llm/provider.py` | Optional. Provider abstraction, prompt, output validation, fallback. |
| Persistence | `db/models.py`, `db/session.py` | Canonical data model, schema initialisation, transactional sessions. |
| Config | `config.py` | `PRODUCTPULSE_*` env vars via pydantic-settings, `.env` locally, Streamlit secrets in the cloud. |

## Data model

```
import_batches 1─* feedback *─1 feedback (duplicate_of)
                     │ 1
                     ├─* theme_memberships *─1 themes *─1 analysis_runs
                     ├─* similarity_pairs (a,b) *─1 analysis_runs
                     └─* opportunity_evidence *─1 opportunities 1─* priority_history
                                                       │ 1
                                                       └─1 roadmap_items
scenarios (JSON inputs per opportunity) · roadmap_snapshots (JSON items) · audit_events · embedding_cache · schema_info
```

Key design points:
- **Feedback IDs are stable**: `fb_` + SHA-256 of (source + source ID), or of (normalised text + date + customer + source) when there's no source ID. Re-imports are idempotent.
- **Originals are preserved**: `text_original` is verbatim; `text_normalized` (NFKC, whitespace collapsed) is used for analysis and hashing.
- **Duplicates are flagged, not deleted**: `duplicate_of_id` marks exact-text repeats, and `similarity_pairs` holds reviewable suggestions.
- **Analysis history**: each run creates new themes. The previous run's themes are deactivated, not deleted, and run-level `stats` snapshot theme sizes for comparison. Manual theme names carry over when membership overlaps by ≥ 50%.
- **Evidence survives re-analysis**: opportunities link to feedback IDs directly, not only to a theme.
- **Integrity**: foreign keys with `ON DELETE CASCADE/SET NULL` (SQLite enforces them via `PRAGMA foreign_keys=ON`), unique constraints (theme/feedback, opportunity/feedback, one roadmap item per opportunity), and indexes on filter columns.
- **Transactions**: `Database.session()` commits on success and rolls back on any exception.

### Schema management

`init_db()` runs `metadata.create_all()` (idempotent) and records `SCHEMA_VERSION` in `schema_info`. On a version mismatch it raises a clear error instead of running against an incompatible schema. That is sufficient for a single-version MVP. Before the first breaking schema change, adopt Alembic and baseline at version 1.

## Analysis pipeline (`analysis/pipeline.py`)

1. **Load** up to `MAX_ANALYSIS_ROWS` most recent records.
2. **Sentiment**: VADER plus a product-feedback lexicon. Score, label and reliability flag go on every record.
3. **Filter**: records with fewer than 3 words are excluded from clustering (counted and shown).
4. **Embed**: Sentence-Transformers (`all-MiniLM-L6-v2`, CPU). Vectors are cached in `embedding_cache` by (text hash, model). If the model can't load, it falls back to TF-IDF + SVD and the reason is recorded in the run notes and shown in the UI.
5. **Cluster**: average-linkage agglomerative clustering on cosine distance (threshold 0.65 semantic / 0.95 lexical), or HDBSCAN above 6,000 records. Zero vectors and groups below the minimum size stay unclustered.
6. **Label**: c-TF-IDF keywords → label. A template problem statement filled with counts.
7. **Trends**: comparison window vs. the mean of the baseline windows, anchored at the latest feedback date.
8. **Similarity**: exact (same hash) and semantic (cosine ≥ threshold, top 5 neighbours per record), ordered original-first.
9. **Record**: run stats, notes and an audit event.

The process-wide `lru_cache` keeps one model instance, so Streamlit reruns and other sessions reuse it. The DB-backed embedding cache means re-analysis after a new import only embeds new texts.

## Optional LLM flow

Clicking **Generate AI summary** calls `themes.generate_ai_summary` → `provider.summarize_theme`, which sends only that theme's (ID, text) pairs (at most 40 records, 600 characters each). The response must be JSON matching `{summary, cited_ids}`. Validation requires that cited IDs belong to the theme and that any quoted passage of 12+ characters appears verbatim in the evidence. Any exception or validation failure keeps the deterministic summary, and the reason is logged with the API key redacted. Stored AI output is cleared automatically if the theme's membership later changes so that citations no longer match.

The Anthropic provider uses the official SDK with low effort and server-side refusal fallback. The OpenAI-compatible provider uses `httpx` with a timeout.

## Security and reliability measures

- Upload limits (size, rows, text length). Binary and non-CSV files are rejected. The `csv` module is used with a bounded field size, and nothing in a file is evaluated.
- CSV exports prefix cells starting with `= + - @ \t \r` with `'` to neutralise spreadsheet formulas.
- All SQL goes through the SQLAlchemy ORM/Core with bound parameters. LIKE wildcards in search input are escaped.
- User text is rendered with native Streamlit components, or `html.escape`d wherever custom HTML is used.
- Secrets come only from the environment or Streamlit secrets, are never displayed, and the database URL is shown with credentials masked.
- Structured JSON-lines logs go to stderr. User-facing errors carry a reference ID instead of tracebacks.

## Deployment design

- **Target:** Streamlit Community Cloud (or any host that can run `streamlit run streamlit_app.py`).
- **Dependencies:** `requirements.txt` pins direct dependencies and pulls CPU-only PyTorch through `--extra-index-url`. It resolves on Linux x86-64 for Python 3.12 and 3.11 (checked with `uv pip compile`).
- **Storage:** SQLite by default (ephemeral on Community Cloud), PostgreSQL via `PRODUCTPULSE_DATABASE_URL` for persistence (`psycopg2-binary` included).
- **Model:** downloaded at first analysis. If the host can't download or run it, set `PRODUCTPULSE_EMBEDDING_BACKEND=tfidf`.
