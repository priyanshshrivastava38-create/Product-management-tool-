# Product metrics — definitions, assumptions, limitations

Every number in the app is computed from stored rows at render time. Nothing is hardcoded, back-filled or estimated unless it is labelled as an assumption.

## Label conventions

| Label | Meaning |
|---|---|
| **Observed** | Verbatim stored feedback (quotes, record text, metadata) |
| **Calculated** | Deterministic computation over stored data |
| **Assumption / Forecast** | Entered or estimated by the team; not measured |
| **AI-generated** | Optional LLM interpretation that passed validation |

## Feedback metrics

| Metric | Definition | Notes |
|---|---|---|
| Feedback records | `count(feedback)` | Includes exact-text duplicates (flagged, not removed) |
| Undated | Records with no `created_at` | Excluded from time series and trends |
| Sentiment label | VADER compound ≥ 0.05 positive, ≤ −0.05 negative, else neutral | Uses a domain lexicon (see below) |
| Sentiment score | VADER compound in [−1, 1] | **Not a probability** |
| Sentiment reliability | **Low**: < 4 words, positive and negative token shares each ≥ 10%, or \|score\| < 0.15. **High**: ≥ 6 words and \|score\| ≥ 0.5. Otherwise **medium** | A heuristic flag, not calibrated confidence |
| Negative share | negative ÷ scored records | Shown with its denominator |

**Sentiment lexicon adjustment.** VADER's general lexicon scores request words ("please", "support", "help", "like") as positive and misses failure words ("crash", "timeout", "broken"). `analysis/sentiment.py` adds about 45 documented valence overrides. On a 50-sentence hand-labelled set, accuracy is 0.62 for plain VADER and 0.94 for the adjusted lexicon (see AI_EVALUATION.md). The same author wrote the lexicon and the test set.

## Theme metrics

| Metric | Definition |
|---|---|
| Records in theme | Active memberships |
| Distinct customers | Distinct non-empty `customer_id` among members |
| Cohesion | Mean cosine similarity of members to the theme centroid |
| Share grouped | Records in any active theme ÷ all records |

### Trend status

The reference date *R* is the latest feedback date in the workspace. The comparison window is (R − W, R]. The baseline is the mean count over the *k* windows before it.

| Status | Rule |
|---|---|
| Not enough history | Data starts after R − W·(k+1) |
| Low volume | Comparison window has fewer than the minimum records |
| Emerging | recent ÷ baseline ≥ growth threshold (or baseline = 0) **and** recent − baseline ≥ 3 |
| Declining | recent ÷ baseline ≤ 1 ÷ growth threshold |
| Stable | otherwise |

Defaults: W = 28 days, k = 3, minimum 5 records, threshold 1.5×. All are configurable per run, and every status comes with a plain-language explanation that includes the numbers.

### Evidence quality (0–6 points)

| Component | Points |
|---|---|
| Volume | ≥ 8 records → 1, ≥ 20 → 2 |
| Customer breadth | ≥ 4 distinct customers → 1, ≥ 10 → 2 (0 if no customer IDs) |
| Source diversity | ≥ 3 sources → 1 |
| Cohesion | ≥ 0.55 (semantic) / ≥ 0.35 (TF-IDF) → 1 |

**Strong** ≥ 5 · **Moderate** 3–4 · **Limited** ≤ 2. This is a transparent rubric for how much evidence exists, not a statistical confidence that the problem is real or important.

## Prioritisation metrics

**RICE = (Reach × Impact × Confidence) ÷ Effort**

| Input | Scale | Validation |
|---|---|---|
| Reach | Customers/accounts per quarter | ≥ 0 |
| Impact | 0.25 minimal · 0.5 low · 1 medium · 2 high · 3 massive | Must be on the scale |
| Confidence | 10–100% in the UI, stored as a fraction | 0 < c ≤ 1; values 1–100 are treated as percentages |
| Effort | Person-months | > 0 (prevents division by zero) |

- **Reach proxy.** When an opportunity is created from a theme, reach defaults to the number of distinct customer IDs in the evidence (or the record count if there are none). It is flagged `reach_is_proxy` with a written basis in the UI, the ranking table and exported briefs. Feedback volume over-represents vocal customers and is never presented as true reach.
- **Default confidence.** 80% when evidence quality is Strong, otherwise 50%. It is a starting point for the team to edit.
- **Ranking.** Descending RICE, with ties sharing a rank. Computed over all opportunities before filters are applied.
- **Sensitivity.** For each opportunity, the rank after moving one input ±X% (impact moves one step on its scale) with the others held fixed. "Stable" means the rank range has width 0.
- **Score history.** A `priority_history` row is written on creation and whenever a RICE input changes (manual edit or scenario restore), along with the reason.

## Roadmap and traceability metrics

| Metric | Definition |
|---|---|
| Themes linked | Active themes referenced by ≥ 1 opportunity |
| Evidence-backed opportunities | Opportunities with ≥ 1 evidence link |
| Records cited as evidence | Distinct feedback IDs in `opportunity_evidence` |
| Roadmap changes | Audit events `roadmap_*`, by day |
| Version diff | Added, removed, moved horizon, reordered, status, target date, RICE change |

## History and snapshots

- Analysis runs store theme sizes and sentiment counts at run time, and two runs can be compared. Themes are matched by name, so renamed or re-clustered themes appear as separate rows.
- Time-series charts are hidden or annotated when the filtered data covers fewer than 56 days.

## Limitations

- Lexicon sentiment is English-centric and misses sarcasm and implicit complaints.
- Clusters depend on wording and the threshold. Users should curate them.
- Trends compare counts. They don't adjust for changes in total feedback volume (for example, a survey launch). Check the volume chart alongside them.
- Customer counts rely on consistent `customer_id` values across sources.
