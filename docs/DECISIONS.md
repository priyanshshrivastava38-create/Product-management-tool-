# Key decisions and trade-offs

| # | Decision | Why | Trade-off |
|---|---|---|---|
| D1 | **Local-first analysis; LLM optional** | The core demo must work without paid keys, and deterministic results are reproducible and explainable in an interview | Summaries are template-based unless an LLM is configured |
| D2 | **Sentence-Transformers `all-MiniLM-L6-v2` on CPU, with a TF-IDF fallback** | Small (~90 MB), fast on CPU, and strong on paraphrases (ARI 0.94 vs 0.54 for TF-IDF) | Needs torch (~200 MB CPU wheel on Linux) and a first-run download. The fallback is much weaker |
| D3 | **CPU-only PyTorch via `--extra-index-url`** | The default Linux torch wheel pulls CUDA libraries (GBs), which is risky on free hosting | Relies on the host installer honouring the extra index. Verified with `uv pip compile` for linux/x86-64 |
| D4 | **Agglomerative clustering with a distance threshold as the default** | No need to choose k, it is deterministic, and it scored best in evaluation. HDBSCAN over-fragmented | O(n²) memory, so it is capped at 6,000 records per run (measured ~420 MB peak) |
| D5 | **Short records (< 3 words) are excluded from clustering, and small groups stay unclustered** | Forcing "ok"/"thanks" into themes produces fake evidence | Some valid terse feedback is not grouped. It is still visible in the Unclustered tab |
| D6 | **c-TF-IDF keyword labels + template problem statements** | Every word of a label can be traced to the data. Nothing is invented | Labels are less fluent than an LLM's. Users can rename |
| D7 | **VADER plus a small documented domain lexicon** | Plain VADER made 38% errors on product feedback (requests read as positive) | The lexicon was tuned in-sample. Sarcasm is still missed |
| D8 | **Duplicates are flagged and reviewed, never merged** | Merging destroys evidence about how many customers hit a problem | Duplicate lists can be long. They are filterable by score, type and review status |
| D9 | **Stable content-derived IDs** | Re-imports are idempotent without asking users for unique IDs | Two different records with the same source ID and source are treated as the same record (by design) |
| D10 | **Reach from feedback is labelled as a *proxy*** | Feedback volume ≠ customers affected, and presenting it as reach would mislead prioritisation | One more field for users to update |
| D11 | **RICE with a fixed ordinal impact scale; sensitivity and scenarios instead of a "smart" score** | Transparent, deterministic and easy to defend. Sensitivity shows how much uncertainty matters | No weighting by revenue (customer value is shown but not scored) |
| D12 | **SQLite by default, PostgreSQL via URL** | Zero-setup local runs, with a durable option for hosting. Both tested | Community Cloud SQLite is ephemeral, which is documented and shown in Settings |
| D13 | **`create_all` + schema version check instead of Alembic** | Single-version MVP, fewer moving parts | A migration tool is needed before the first breaking schema change |
| D14 | **Synchronous analysis with a progress bar (no Celery/Redis)** | ~5–15 s for the sample dataset is acceptable, and it avoids extra infrastructure | Long analyses block that user's session |
| D15 | **Markdown/CSV export, no PDF** | Reliable on any host, easy to paste into Confluence/Notion/Docs, no system font or renderer dependencies | Users convert to PDF themselves |
| D16 | **Streamlit multipage via `st.navigation`; segmented control on the prioritisation page** | Function-based pages share one entry point. Streamlit tabs can't be selected programmatically, and the "create opportunity → edit it" flow needed that | Some Streamlit idioms (the rerun model) require care; covered by the browser journey test |
| D17 | **LLM validation: cited IDs ⊆ theme, quoted text ⊆ evidence, fallback on anything else** | Prevents the most damaging failure: invented quotes or citations | Cannot detect unsupported paraphrases, so AI text is always labelled |
| D18 | **Synthetic sample data with separate ground truth** | Safe to publish, gives a demo immediately, and allows measurable evaluation | Templated data flatters every metric (see AI_EVALUATION.md) |
