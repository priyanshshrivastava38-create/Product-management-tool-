# PRD — ProductPulse AI (MVP)

## 1. Problem

Product teams get feedback from support tickets, app-store and G2 reviews, NPS surveys, interviews and customer-success notes. That feedback is:

- **Scattered** across tools, with different formats and column names.
- **Repetitive**: the same problem shows up in dozens of different wordings.
- **Hard to quantify**: it's unclear how many customers, which segments, or whether a problem is growing.
- **Disconnected from decisions**: roadmap items rarely cite the feedback that justified them, and AI summaries make this worse when they invent quotes or numbers.

The result is that prioritisation is driven by the loudest anecdote or the most recent escalation rather than by evidence.

## 2. Goal

Help a product team go from raw feedback to an evidence-backed prioritised decision in one session. Every claim along the way must be traceable to real records.

**Product principle:** AI may assist analysis, but every important insight remains traceable to stored customer feedback. No fabricated evidence, quotes or metrics.

## 3. Personas

| Persona | Needs | Key screens |
|---|---|---|
| **Priya, Product Manager (B2B SaaS)** | Find the top problems, build a defensible case, and prioritise and communicate it | Problem Themes, Prioritize, Roadmap, brief export |
| **Marco, Head of Product** | See at a glance what's hurting customers, what's growing, and whether the roadmap reflects it | Home, Trends |
| **Dana, Customer Success / Support lead** | Bring ticket and CSM-note exports in and see whether their escalations show up in the plan | Import & Data, Feedback Explorer |
| **Recruiter / interviewer** | Explore a working demo without setup or API keys | Sample workspace, every page |

## 4. User stories

1. As a PM, I can upload a CSV with my own column names and map them, so I don't have to reformat exports.
2. As a PM, I see which rows were rejected and why, and can download them, so no feedback disappears silently.
3. As a PM, I can re-import an updated export without duplicating records.
4. As a PM, I see feedback grouped into problem themes with labels I can rename, merge or split.
5. As a PM, every theme shows verbatim quotes and links to the source records, so I can verify the theme is real.
6. As a PM, I see which themes are growing compared with an explained baseline, and the app says so when history is insufficient.
7. As a PM, I can review exact and near-duplicate requests without the tool merging anything on its own.
8. As a PM, I can turn a theme into an opportunity with its evidence attached and a clearly labelled reach *proxy*.
9. As a PM, I can edit RICE inputs and see the score, formula and ranking update immediately.
10. As a PM, I can see how sensitive the ranking is to uncertain estimates and save alternative scenarios.
11. As a Head of Product, I can see the roadmap by horizon, compare versions, and see what changed.
12. As a PM, I can export a product brief that separates observed facts, calculations, assumptions and AI interpretations.
13. As anyone, I can see how every number is calculated and what data is stored.

## 5. MVP scope

**In scope:** CSV import with mapping and validation; sentiment; semantic clustering with a fallback; duplicate detection; trend detection; theme curation; evidence-quality rubric; optional validated LLM summaries; opportunities with RICE, sensitivity and scenarios; roadmap with versions; Markdown/CSV exports; analytics and audit log; a synthetic sample workspace; SQLite locally and PostgreSQL optionally.

**Non-goals (MVP):**
- Direct integrations (Zendesk, Intercom, App Store APIs). CSV is the integration surface.
- Multi-user accounts, roles, SSO, or real-time collaboration.
- Background job queues. Analysis runs synchronously with a progress bar.
- Non-English sentiment, aspect-level sentiment, or fine-tuned models.
- PDF export. Markdown converts reliably and avoids rendering dependencies on the host.
- Forecasting business impact. Expected outcomes are labelled as hypotheses.

## 6. Acceptance criteria

| # | Criterion | Verified by |
|---|---|---|
| A1 | The app starts with no environment variables and no API keys | `scripts/healthcheck.py`; fresh-venv run |
| A2 | The sample workspace loads and is analysed from the dashboard in one click | `scripts/ui_journey.py --full-sample` |
| A3 | Uploading a CSV shows a preview, mapping, valid/rejected counts and downloadable rejections | `tests/test_csv_import.py`; UI journey |
| A4 | Re-importing the same file inserts 0 new records | `test_repeat_import_does_not_duplicate` |
| A5 | Every quote shown is byte-identical to a stored record and links to it | `test_evidence_is_traceable_to_stored_records`; UI journey |
| A6 | An invalid LLM output (bad JSON, unknown IDs, invented quote, timeout) never reaches the UI | `tests/test_llm.py` |
| A7 | RICE rejects effort ≤ 0 and off-scale impact, and normalises confidence percentages | `tests/test_prioritization.py` |
| A8 | Reach derived from feedback is flagged as a proxy in the UI and in exports | `test_suggest_from_theme_labels_reach_as_proxy`; brief |
| A9 | Roadmap versions can be saved and compared (added/removed/moved/reordered/status/date/score) | `test_snapshots_and_compare` |
| A10 | The product brief has all nine sections and labels forecasts and assumptions | `test_product_brief_has_all_sections_and_verbatim_quotes` |
| A11 | Trend detection reports “not enough history” instead of guessing | `tests/test_trends.py` |
| A12 | Every page renders without exceptions, both empty and populated | `tests/test_app_pages.py` |

## 7. Success metrics for the product (if deployed with real users)

These are definitions, not results. No user data exists for this MVP.

- **Time to first evidence-backed opportunity**: from upload to the first opportunity with ≥ 1 evidence record.
- **Traceability rate**: share of roadmap items whose opportunity has linked evidence.
- **Curation rate**: share of themes renamed, merged or split, as a proxy for clustering usefulness (too high suggests poor clusters).
- **Brief exports per roadmap version**: whether the output is used for communication.
