# Product roadmap (for ProductPulse AI itself)

## Completed — MVP

- CSV ingestion: column mapping, validation, rejected-row export, idempotent re-import, import history and deletion
- Analysis: sentiment (VADER + domain lexicon), semantic embeddings with DB cache and TF-IDF fallback, clustering, c-TF-IDF labels, near-duplicate detection, emerging-problem detection
- Evidence-backed insight pages with verified verbatim quotes, an evidence-quality rubric, and theme rename/merge/split/remove
- Optional LLM summaries with citation and quote validation and deterministic fallback
- Opportunities with live RICE, proxy-reach labelling, sensitivity analysis, saved/compared/restored scenarios and score history
- Roadmap with horizons, ordering, metadata, versions and diffs; Markdown/CSV exports; nine-section product brief
- Analytics (volume, sentiment, theme changes, priority/roadmap history, traceability Sankey, run comparison) and audit log
- SQLite or PostgreSQL; health check; evaluation script; 100+ automated tests; browser journey test

## Known limitations

See [AI_EVALUATION.md §7](AI_EVALUATION.md#7-failure-cases-to-know-about) and the README. The main ones:
- English-only sentiment, with sarcasm missed
- Unsupervised themes need curation; TF-IDF fallback is weak
- No authentication or multi-tenant separation
- Analysis runs synchronously; capped at 6,000 records per run
- Trends compare raw counts and do not normalise for total volume

## Next iterations (candidates, not commitments)

| Horizon | Idea | Why |
|---|---|---|
| Next | Normalised trends (share of total) plus a volume-shift warning | Avoid false "emerging" signals during survey campaigns |
| Next | Multi-label assignment for mixed-topic feedback | One record can mention two problems |
| Next | Real-data evaluation set with held-out sentiment labels | Replace author-made metrics with honest accuracy |
| Next | Reach import from product analytics (CSV of accounts per feature) | Replace proxy reach with measured reach |
| Later | Connectors (Zendesk, Intercom, App Store, Typeform) | Remove manual CSV exports |
| Later | Background analysis jobs and incremental clustering | Larger datasets without blocking the UI |
| Later | Authentication, workspaces and roles | Team use |
| Later | Alembic migrations | Safe schema evolution |
| Later | Multilingual embeddings and sentiment | Global customer bases |
| Later | LLM-assisted opportunity drafting (with the same citation validation) | Faster brief writing while staying traceable |
