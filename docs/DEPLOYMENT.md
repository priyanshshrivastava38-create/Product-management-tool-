# Deployment

**Target: Streamlit Community Cloud.** Any Python host that can run `streamlit run streamlit_app.py` also works.

## What was verified locally (2026-10-11)

| Check | Result |
|---|---|
| Fresh virtualenv, `pip install -r requirements.txt` (macOS arm64, Python 3.12.4) | Installed in ~105 s; `pip check`: no broken requirements; venv ≈ 1.3 GB |
| `uv pip compile requirements.txt --python-platform x86_64-manylinux_2_28` for Python 3.12 and 3.11 | Resolves; `torch==2.5.1+cpu`, no NVIDIA/CUDA packages |
| `python scripts/healthcheck.py --with-model` in the fresh env | All checks pass |
| `streamlit run streamlit_app.py` from the fresh env on an empty database + `scripts/ui_journey.py --full-sample` | Journey passes; no server errors |
| No required environment variables; no API key needed | Confirmed (health check “Configuration loads”) |
| No dependency on files outside the repository | Sample data, config and model download only (model from Hugging Face at first analysis) |
| PostgreSQL 16 (`tests/test_postgres.py` on a throwaway database) | Pass |

**Not verified:** an actual deployment to Streamlit Community Cloud. That requires your GitHub and Streamlit accounts, and no public URL exists yet. The Linux resolution check above is the closest local proxy for the cloud installer.

## Streamlit Community Cloud — steps

1. **Push to GitHub.** `.gitignore` already excludes `.env`, `.streamlit/secrets.toml`, local databases, caches and virtualenvs. From the repo root:
   ```bash
   git add -A && git commit -m "ProductPulse AI"
   gh repo create productpulse-ai --public --source . --push   # or create the repo in the GitHub UI and push
   ```
2. Go to **share.streamlit.io → Create app → Deploy a public app from GitHub**.
   - Repository: your repo · Branch: `main` · Main file path: `streamlit_app.py`
   - **Advanced settings → Python version: 3.12**
3. **Secrets (optional)**, in TOML:
   ```toml
   # Durable storage (recommended if you want data to survive restarts)
   PRODUCTPULSE_DATABASE_URL = "postgresql+psycopg2://USER:PASSWORD@HOST:5432/DB?sslmode=require"
   # Optional LLM summaries
   PRODUCTPULSE_LLM_PROVIDER = "anthropic"
   PRODUCTPULSE_LLM_API_KEY = "..."
   ```
   `streamlit_app.py` copies `PRODUCTPULSE_*` secrets into environment variables at startup.
4. Deploy. The first build installs dependencies (several minutes). When the app is up, open it, click **Try it with sample data**, and wait for the first analysis, which includes the ~90 MB model download.
5. Verify with the checklist below.

### Post-deploy checklist

- [ ] Dashboard shows the welcome state with no errors
- [ ] Try it with sample data → dashboard shows themes and emerging problems
- [ ] Problem Themes → a theme shows quotes; *Open record* opens the record
- [ ] Create an opportunity → change effort → the score updates → Save
- [ ] Roadmap → add → Export → download the product brief
- [ ] Settings → Configuration shows the expected database (credentials masked) and embedding backend
- [ ] Import & Data → upload `messy_feedback_example.csv` → mapping and rejected rows shown

## Storage and persistence

| Option | Persistence | Configure |
|---|---|---|
| SQLite (default) | **Ephemeral on Community Cloud.** Wiped on restart, redeploy or sleep. Persistent on your own server/disk | Nothing (`data/productpulse.db`) |
| PostgreSQL (Neon, Supabase, RDS, Render, …) | Durable | `PRODUCTPULSE_DATABASE_URL=postgresql+psycopg2://…` (`postgres://` URLs are rewritten automatically) |

The schema is created automatically on first start. A schema-version mismatch stops the app with a clear message rather than corrupting data. Point the app at an **empty, dedicated** database: *Reset workspace* deletes all rows in the app's tables.

## Resource notes

- Memory (measured locally): importing and semantically analysing the 1.4k-record sample peaks at ~540 MB RSS for the Python process (model included, Streamlit server overhead not included). Clustering is capped at 6,000 records per run (~420 MB peak measured with TF-IDF vectors at that size).
- If the host runs out of memory or can't reach Hugging Face, set `PRODUCTPULSE_EMBEDDING_BACKEND = "tfidf"`. Everything still works with lower-quality themes, and the UI shows which method was used.
- Optional model pre-download (self-hosted): `python -c "from sentence_transformers import SentenceTransformer as S; S('sentence-transformers/all-MiniLM-L6-v2')"`.

## Other hosts

Any container or VM: install the requirements and run

```bash
streamlit run streamlit_app.py --server.port $PORT --server.address 0.0.0.0
```

Attach a persistent volume for SQLite or use PostgreSQL. The health endpoint is `/_stcore/health`. Run `python scripts/healthcheck.py --url https://your-host` after deploying.

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `pyarrow requires NumPy 2.0 or newer` | Mixed NumPy pins. Use the committed `requirements.txt` (numpy 2.1.3, pyarrow 26.0.0) |
| Build tries to download CUDA libraries | The host ignored `--extra-index-url`. Pin `torch==2.5.1+cpu` with the index line, or switch to `tfidf` and remove torch/sentence-transformers |
| "Semantic model unavailable, used TF-IDF fallback" banner | The model couldn't be downloaded or loaded. Check outbound network access or memory. The app keeps working |
| Data disappears after a while on Community Cloud | Ephemeral SQLite. Configure PostgreSQL |
| `Database schema version … does not match` | The DB was created by another schema version. Use a fresh database or reset |
| Upload rejected as too large | Raise both `PRODUCTPULSE_MAX_UPLOAD_MB` and `server.maxUploadSize` in `.streamlit/config.toml` |
| AI summary button missing | Set both `PRODUCTPULSE_LLM_PROVIDER` and `PRODUCTPULSE_LLM_API_KEY` |
