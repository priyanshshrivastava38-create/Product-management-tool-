# AI evaluation

All numbers below were produced by `python scripts/evaluate.py` on an 8 GB Apple M1 (CPU only) on 2026-10-11. Rerun the script to reproduce them. Timings on this machine are affected by memory pressure and are indicative only.

> **Read these results with care.** Both evaluation sets were created by the project author: the synthetic dataset is generated from templates, and the sentiment set is hand-labelled. They show that the pipeline behaves as designed on controlled data. They do **not** measure accuracy on real customer feedback, which is messier, multilingual and less templated.

## 1. Datasets

| Dataset | Size | Labels | Used for |
|---|---|---|---|
| `data/sample/synthetic_feedback.csv` | 1,376 records, 26 weeks | Generating theme in `synthetic_ground_truth.csv` (13 themes + noise). Never imported by the app | Clustering, similarity, trends, traceability |
| `tests/data/sentiment_eval.csv` | 50 sentences (24 neg / 14 neutral / 12 pos) | Hand-labelled by the author; written separately from the synthetic templates and includes sarcasm | Sentiment |

The synthetic generator paraphrases each theme 5–7 ways and adds random openers/closers, metadata gaps (1% undated, 3% no segment, 5% no area), ~2% verbatim repeats and ~3% very short "noise" answers. Two themes (`android_crash`, `dashboard_slow`) spike in the final four weeks, one grows linearly and one declines.

## 2. Sentiment

| Method | Accuracy | Recall neg / neutral / pos |
|---|---|---|
| VADER (default lexicon) | 0.62 | 0.54 / 0.50 / 0.92 |
| VADER + product-feedback lexicon (shipped) | **0.94** | 0.88 / 1.00 / 1.00 |

The default lexicon's main failure was polite requests turning positive ("Please add…", "We need SAML support…") and failure words being unknown ("crashes", "unusable", "corrupted").

**Remaining failures (known limitations):**
- "The app freezes every time I attach a file, thanks for nothing." → neutral (sarcasm)
- "The import keeps timing out for files over 5MB, would love a fix." → positive ("love" outweighs the complaint)
- "Great, the dashboard crashed again." → positive (sarcasm; asserted as a known failure in `tests/test_sentiment.py`)

**Caveat:** the lexicon adjustments were chosen while looking at this set, so 0.94 is an optimistic, in-sample figure. A held-out real-data sample is needed before trusting it.

## 3. Clustering (themes)

Metrics compare predicted themes with the generating theme for the 1,345 non-noise records (ARI = adjusted Rand index, NMI = normalised mutual information; 1.0 = perfect).

| Embedding + clustering | Distance threshold | Themes found (13 true) | ARI | NMI | Time |
|---|---|---|---|---|---|
| TF-IDF/LSA + agglomerative (fallback default) | 0.95 | 32 | 0.538 | 0.769 | 0.9 s |
| MiniLM + agglomerative | 0.55 | 26 | 0.860 | 0.918 | 12.5 s* |
| **MiniLM + agglomerative (default)** | **0.65** | **18** | **0.943** | **0.960** | 5.0 s |
| MiniLM + agglomerative | 0.75 | 9 | 0.575 | 0.839 | 5.2 s |
| MiniLM + HDBSCAN (min size = 1% of records) | — | 60 | 0.312 | 0.752 | 5.9 s |

\*includes model warm-up.

Observations:
- **The defaults come from this sweep.** 0.65 is the best tested setting. At 0.75, distinct themes merge (ARI drops sharply), so the threshold is exposed in the UI.
- **18 vs 13 themes:** the extra themes are mostly sub-variants of one true theme (for example "users struggle to start" split from the onboarding theme). That's acceptable because users can merge themes, and the curation tools exist for this reason.
- **TF-IDF fallback groups by wording, not meaning.** It splits one problem into several phrasing clusters (32 themes). It is a working fallback, and the UI shows a warning when it was used.
- **HDBSCAN over-fragments this data** (60 micro-themes) even with the minimum size scaled to 1% of records. So `auto` uses agglomerative up to 6,000 records (measured 3.6 s and ~420 MB peak RSS at 6,000 records with TF-IDF vectors), and HDBSCAN only beyond that.
- **Noise handling:** 100% of the generated noise records ("ok", "n/a", "Thanks"…) were kept out of themes. They are excluded by the ≥ 3-word rule (31 records), and zero vectors are never clustered.

## 4. Duplicate and similar-request detection

| Embeddings | Semantic pairs ≥ 0.90 | Pairs whose records share a ground-truth theme |
|---|---|---|
| MiniLM | 3,898 | 100% |
| TF-IDF/LSA | 1,819 | 100% |

This measures *topical* precision: no proposed pair crossed themes. It does **not** measure whether pairs are true duplicates. Because the synthetic data is templated, many near-duplicates really are the same sentence with a different opener, so these numbers are optimistic. Recall was not measured, because there is no labelled set of all true duplicate pairs. Pairs are suggestions for human review and are never merged automatically.

## 5. Evidence traceability

| Check | Result | Where |
|---|---|---|
| Every representative quote is byte-identical to a stored `text_original` and belongs to the theme | Pass (all themes, all configurations above) | `evaluate.py`, `test_evidence_is_traceable_to_stored_records` |
| Brief quotes appear verbatim in stored records | Pass | `test_product_brief_has_all_sections_and_verbatim_quotes` |
| UI quotes link to the source record | Pass (browser) | `scripts/ui_journey.py` |
| Opportunity evidence must reference existing feedback IDs | Enforced (raises otherwise) | `test_evidence_must_reference_real_feedback` |
| Displayed quotes are re-verified against the DB before rendering | Enforced; quotes are hidden if verification fails | `insights.py` → `themes.verify_quotes` |

## 6. LLM output validation (mocked)

The app was not run against a live LLM in this build; no API key was used. The validation and fallback logic is tested with mocked responses in `tests/test_llm.py`:

| Case | Expected | Result |
|---|---|---|
| Valid JSON citing theme records | Accepted, stored as AI-generated | Pass |
| JSON inside a code fence | Accepted | Pass |
| Not JSON / schema violation / empty citations | Fallback to deterministic | Pass |
| Cites an ID outside the theme | Fallback | Pass |
| Quotes text that isn't in the evidence | Fallback | Pass |
| Verbatim quote from the evidence | Accepted | Pass |
| Timeout / HTTP 500 | Fallback; API key redacted from the reason | Pass |

Not covered: how good real LLM summaries are, and paraphrased (unquoted) fabrications. The validator can only catch invented IDs and invented *quoted* text, not an unsupported paraphrase. That's why AI text is always labelled and shown next to the evidence.

## 7. Failure cases to know about

1. **Sarcasm and polite complaints** get mis-scored (see §2).
2. **Over-splitting** of one problem into sub-themes, especially with TF-IDF. Use merge.
3. **Mixed-topic feedback** ("export is slow and also please add dark mode") is assigned to a single theme.
4. **Trend artefacts:** a sudden survey campaign raises counts across themes. Trends compare raw counts, not shares.
5. **Very small datasets** (< 8 analysable records with default settings) produce no themes, and the app says so instead of forcing groups.
6. **Non-English text:** MiniLM is English-centric and VADER is English-only.
