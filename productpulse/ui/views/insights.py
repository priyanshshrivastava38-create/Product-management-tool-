"""AI Insights — run analysis, inspect evidence-backed themes, curate them, review duplicates."""

from __future__ import annotations

import html

import pandas as pd
import plotly.express as px
import streamlit as st
from sqlalchemy import select

from productpulse.analysis.pipeline import AnalysisParams
from productpulse.analysis.trends import TrendParams
from productpulse.db.models import Feedback, SimilarityPair, Theme, ThemeMembership
from productpulse.services import feedback as fb
from productpulse.services import habits, prioritization, themes, workspace
from productpulse.ui import state, theme
from productpulse.ui.views.common import no_data_notice, theme_summaries


def _analysis_controls(run) -> None:
    s = state.settings()
    with st.expander("Re-run analysis (advanced settings)", expanded=False, icon=":material/tune:"):
        c1, c2, c3 = st.columns(3)
        backend = c1.selectbox(
            "Embedding method",
            ["auto", "sentence-transformers", "tfidf"],
            index=["auto", "sentence-transformers", "tfidf"].index(s.embedding_backend),
            help="auto = local Sentence-Transformers model, falling back to TF-IDF if unavailable.",
        )
        algorithm = c2.selectbox(
            "Clustering algorithm",
            ["auto", "agglomerative", "hdbscan"],
            help="auto = agglomerative up to 6,000 records, HDBSCAN beyond (HDBSCAN fragments themes more; see docs).",
        )
        use_default = c3.checkbox(
            "Default distance threshold", value=True, help="0.65 for semantic embeddings, 0.95 for TF-IDF."
        )
        threshold = c3.slider(
            "Distance threshold",
            0.3,
            1.2,
            0.65,
            0.05,
            disabled=use_default,
            help="Lower = tighter, more numerous themes. Agglomerative only.",
        )
        c4, c5, c6 = st.columns(3)
        min_size = c4.number_input(
            "Minimum theme size", 2, 100, 4, help="Smaller groups are left unclustered rather than forced into a theme."
        )
        sim = c5.slider(
            "Duplicate similarity threshold",
            0.70,
            0.99,
            0.90,
            0.01,
            help="Cosine similarity at or above which two records are proposed as near-duplicates.",
        )
        window = c6.number_input("Trend window (days)", 7, 180, 28)
        c7, c8, c9 = st.columns(3)
        baseline = c7.number_input(
            "Baseline windows", 1, 12, 3, help="Number of earlier windows averaged as the baseline."
        )
        min_rec = c8.number_input("Min. records in window", 2, 500, 5)
        growth = c9.number_input("Growth threshold (×)", 1.1, 10.0, 1.5, 0.1)
        if st.button("Run analysis", type="primary"):
            try:
                params = AnalysisParams(
                    embedding_backend=backend,
                    algorithm=algorithm,
                    distance_threshold=None if use_default else threshold,
                    min_cluster_size=int(min_size),
                    similarity_threshold=sim,
                    trend=TrendParams(
                        window_days=int(window),
                        baseline_windows=int(baseline),
                        min_records=int(min_rec),
                        growth_threshold=float(growth),
                    ),
                )
            except Exception as exc:
                st.error(f"Invalid settings: {exc}")
                return
            new = state.analyze(params)
            if new:
                st.session_state["flash"] = f"Analysis run #{new.id}: {new.n_themes} themes."
                st.session_state.pop("selected_theme_id", None)
                st.rerun()


def _trend_chart(members: list[Feedback], trend: dict) -> None:
    dates = pd.Series([m.created_at for m in members if m.created_at])
    if dates.empty:
        st.caption("No dated records in this theme.")
        return
    weekly = dates.dt.to_period("W").dt.start_time.value_counts().sort_index().reset_index()
    weekly.columns = ["Week", "Records"]
    fig = px.bar(weekly, x="Week", y="Records")
    if trend.get("recent_start"):
        fig.add_vrect(
            x0=trend["recent_start"],
            x1=trend["reference_end"],
            fillcolor="#4F46E5",
            opacity=0.08,
            line_width=0,
            annotation_text="comparison window",
            annotation_position="top left",
        )
        fig.add_vrect(
            x0=trend["baseline_start"],
            x1=trend["recent_start"],
            fillcolor="#9AA5B1",
            opacity=0.08,
            line_width=0,
            annotation_text="baseline",
            annotation_position="top left",
        )
    fig.update_layout(height=250, xaxis_title=None, title="Weekly volume")
    st.plotly_chart(fig, use_container_width=True)


def _create_opportunity_button(s, t, quality_level: str, key: str) -> None:
    if st.button(
        "Create opportunity",
        icon=":material/add_circle:",
        type="primary",
        key=key,
        use_container_width=True,
        help="Turns this problem into a prioritisable opportunity, with every quote attached as evidence.",
    ):
        data, evidence = prioritization.suggest_from_theme(s, t, quality_level)
        opp = state.run_action(lambda ss: prioritization.create_opportunity(ss, data, evidence, t.id), rerun=False)
        if opp is not None:
            state.go(
                "prioritization",
                selected_opportunity_id=opp.id,
                prio_view="Edit opportunity",
                flash=f"Opportunity “{opp.name}” created with {len(evidence)} customer quotes as evidence. "
                "Check the RICE inputs — reach is an estimate from feedback.",
            )


def _theme_detail(s, t) -> None:
    ins = themes.insight(s, t)
    run = ins["run"]
    n = ins["n"]
    q = ins["quality"]
    trend = t.trend or {}

    title_col, action_col = st.columns([3, 1.1])
    with title_col:
        st.markdown(f"### {html.escape(t.label)}", unsafe_allow_html=True)
        if t.label_edited and t.auto_label and t.auto_label != t.label:
            st.caption(f"Renamed by you · original label: {t.auto_label}")
    with action_col:
        _create_opportunity_button(s, t, q.level, f"mkopp_{t.id}")
        if habits.is_watched(s, t):
            if st.button(
                "Watching",
                icon=":material/visibility:",
                key=f"unwatch_{t.id}",
                use_container_width=True,
                help="You follow this problem on Home. Click to stop watching.",
            ):
                state.run_action(lambda ss: habits.unwatch(ss, t.label), "Stopped watching.")
        elif st.button(
            "Watch",
            icon=":material/visibility:",
            key=f"watch_{t.id}",
            use_container_width=True,
            help="Follow this problem — its latest numbers appear on Home every time you visit.",
        ):
            state.run_action(lambda ss: habits.watch(ss, t.id), f"Watching “{t.label}”. You'll see it on Home.")

    st.write(t.problem_statement)
    theme.cards(
        [
            (
                "Mentions",
                f"{n:,}",
                f"from {ins['customers']} customers" if ins["customers"] else "customer IDs not provided",
                "forum",
            ),
            (
                "Unhappy",
                theme.pct(ins["sentiment"]["negative"], n),
                "of these mentions are negative",
                "sentiment_dissatisfied",
                "red",
            ),
            (
                "Trend",
                theme.TREND_LABELS.get(trend.get("status"), "—"),
                f"{trend.get('recent', 0)} in the last {trend.get('window_days', '?')} days",
                theme.TREND_ICON.get(trend.get("status"), "timeline"),
                "amber" if trend.get("status") == "emerging" else "",
            ),
            (
                "Evidence strength",
                q.level,
                f"{q.points} of 6 points",
                "verified",
                "green" if q.level == "Strong" else "",
            ),
        ]
    )
    st.caption(trend.get("explanation", ""))

    if t.ai_summary or state.settings().llm_enabled:
        with st.container(border=True):
            st.markdown(theme.badge("ai") + " **AI summary**", unsafe_allow_html=True)
            if t.ai_summary:
                st.write(t.ai_summary)
                st.caption(
                    f"{t.ai_provider} · based only on {len(t.ai_cited_ids or [])} quotes from this theme "
                    "(every cited ID verified)."
                )
            if st.button("Generate AI summary" if not t.ai_summary else "Regenerate", key=f"ai_{t.id}"):
                with st.spinner("Asking the model…"):
                    out = state.run_action(
                        lambda ss: themes.generate_ai_summary(ss, state.settings(), t.id), rerun=False
                    )
                if out is not None:
                    if out.used_fallback:
                        st.warning(f"The AI summary didn't pass our evidence checks ({out.reason}), so it wasn't used.")
                    else:
                        st.session_state["flash"] = "AI summary generated and checked against the evidence."
                        st.rerun()

    t_say, t_who, t_sim, t_how = st.tabs(
        [
            ":material/format_quote: What customers say",
            ":material/groups: Who & when",
            ":material/content_copy: Similar requests",
            ":material/edit: Edit & details",
        ]
    )
    with t_say:
        if not themes.verify_quotes(s, ins["quotes"]):
            st.error("Quote verification failed — quotes are hidden.")
        else:
            st.caption("The most typical feedback in this theme, word for word.")
            for qfb in ins["quotes"]:
                meta = " · ".join(
                    x
                    for x in (
                        qfb.source,
                        qfb.customer_segment,
                        qfb.created_at.strftime("%b %d, %Y") if qfb.created_at else None,
                        qfb.id,
                    )
                    if x
                )
                theme.quote(qfb.text_original, meta)
                if st.button("Open record", key=f"q_{t.id}_{qfb.id}"):
                    state.go("explorer", selected_feedback_id=qfb.id)
        st.write("")
        st.markdown(f"**All {n} pieces of feedback in this theme**")
        feel_labels = theme.FEELING_LABELS
        f1, f2 = st.columns([1, 1.4])
        feel = (
            f1.pills(
                "Feeling", list(feel_labels), format_func=feel_labels.get, selection_mode="multi", key=f"feel_{t.id}"
            )
            or []
        )
        segs = sorted({m.feedback.customer_segment for m in t.memberships if m.feedback.customer_segment})
        seg = f2.pills("Segment", segs, selection_mode="multi", key=f"seg_{t.id}") or []
        _members_table(t, f"evidence_{t.id}", feelings=feel, segments=seg)
        if st.button("Open in feedback explorer", key=f"exp_{t.id}"):
            state.go("explorer", explorer_theme=t.id)
    with t_who:
        c1, c2 = st.columns([1.4, 1])
        with c1:
            _trend_chart(ins["members"], trend)
        with c2:
            sdf = pd.DataFrame({"Sentiment": list(ins["sentiment"]), "Records": list(ins["sentiment"].values())})
            fig = px.bar(
                sdf,
                x="Records",
                y="Sentiment",
                orientation="h",
                color="Sentiment",
                color_discrete_map=theme.SENTIMENT_COLORS,
                title="How they feel",
            )
            fig.update_layout(height=250, showlegend=False, yaxis_title=None)
            st.plotly_chart(fig, use_container_width=True)
        c3, c4 = st.columns(2)
        for col, key, title in ((c3, "segments", "Customer segments"), (c4, "product_areas", "Product areas")):
            with col:
                data = ins[key]
                if data:
                    fig = px.bar(
                        pd.DataFrame(data, columns=[title, "Records"]),
                        x="Records",
                        y=title,
                        orientation="h",
                        title=title,
                    )
                    fig.update_layout(height=220, yaxis_title=None, yaxis=dict(autorange="reversed"))
                    st.plotly_chart(fig, use_container_width=True)
                else:
                    st.caption(f"No {title.lower()} information in this feedback.")
        if ins["sources"]:
            st.caption("Sources: " + ", ".join(f"{src} ({c})" for src, c in ins["sources"]))
    with t_sim:
        pairs = ins["pairs"]
        st.caption("Feedback that says nearly the same thing. Nothing is merged — review and keep both.")
        if pairs:
            _pairs_table(s, pairs[:20], key=f"tp_{t.id}")
        else:
            st.caption("No near-duplicates above the similarity threshold.")
    with t_how:
        st.markdown("**Rename**")
        r1, r2 = st.columns([3, 1])
        new_name = r1.text_input("Theme name", value=t.label, key=f"name_{t.id}", label_visibility="collapsed")
        if r2.button("Save name", key=f"rn_{t.id}", use_container_width=True) and new_name != t.label:
            state.run_action(lambda ss: themes.rename(ss, t.id, new_name), "Theme renamed.")
        st.markdown("**Merge** — combine other themes that describe the same problem into this one")
        others = {o.id: o.label for o in themes.active_themes(s) if o.id != t.id}
        m1, m2 = st.columns([3, 1])
        merge_ids = m1.multiselect(
            "Themes to merge",
            list(others),
            format_func=others.get,
            key=f"merge_{t.id}",
            label_visibility="collapsed",
            placeholder="Choose themes to merge in…",
        )
        if m2.button("Merge", key=f"mg_{t.id}", disabled=not merge_ids, use_container_width=True):
            state.run_action(lambda ss: themes.merge(ss, t.id, merge_ids), f"Merged {len(merge_ids)} theme(s).")
        st.markdown("**Split or clean up** — tick feedback below, then move it to a new theme or remove it")
        picked = _members_table(t, f"curate_{t.id}", selectable=True)
        s1, s2, s3 = st.columns([2, 1, 1])
        split_name = s1.text_input(
            "New theme name",
            key=f"splitname_{t.id}",
            placeholder="Name for the new theme",
            label_visibility="collapsed",
        )
        if s2.button(f"Split {len(picked)} out", key=f"sp_{t.id}", disabled=not picked, use_container_width=True):
            state.run_action(lambda ss: themes.split(ss, t.id, picked, split_name), "Created a new theme.")
        if s3.button(f"Remove {len(picked)}", key=f"rm_{t.id}", disabled=not picked, use_container_width=True):
            state.run_action(lambda ss: themes.remove_members(ss, t.id, picked), "Removed from the theme.")
        st.divider()
        st.markdown("**How “evidence strength” is scored**")
        for k, v in q.components.items():
            st.markdown(f"- {k}: {v}")
        st.caption(
            "Strong = 5–6 points, Moderate = 3–4, Limited = 0–2. It measures how much evidence exists, "
            "not whether the problem matters."
        )
        st.caption(
            f"Found with {run.embedding_method} + {run.clustering_method} clustering (run #{run.id}). "
            f"Updated {t.updated_at:%Y-%m-%d %H:%M} UTC."
        )


def _members_table(
    t, key: str, selectable: bool = False, feelings: list[str] | None = None, segments: list[str] | None = None
) -> list[str]:
    members = sorted(t.memberships, key=lambda m: -m.centroid_similarity)
    if feelings:
        members = [m for m in members if m.feedback.sentiment_label in feelings]
    if segments:
        members = [m for m in members if m.feedback.customer_segment in segments]
    if not members:
        st.caption("No feedback matches these chips — click one again to clear it.")
        return []
    edf = pd.DataFrame(
        [
            {
                "Feedback": m.feedback.text_original,
                "Feeling": m.feedback.sentiment_label,
                "Source": m.feedback.source,
                "Segment": m.feedback.customer_segment,
                "Date": m.feedback.created_at,
                "ID": m.feedback.id,
            }
            for m in members
        ]
    )
    ev = st.dataframe(
        edf,
        hide_index=True,
        use_container_width=True,
        height=280,
        key=key,
        on_select="rerun" if selectable else "ignore",
        selection_mode="multi-row",
        column_config={
            "Feedback": st.column_config.TextColumn(width="large"),
            "Date": st.column_config.DatetimeColumn(format="MMM D, YYYY"),
        },
    )
    if not selectable:
        return []
    return [edf.iloc[i]["ID"] for i in (ev.selection.rows if ev and ev.selection else [])]


def _pairs_table(s, pairs: list[SimilarityPair], key: str) -> None:
    ids = {p.feedback_a_id for p in pairs} | {p.feedback_b_id for p in pairs}
    recs = {r.id: r for r in s.scalars(select(Feedback).where(Feedback.id.in_(ids)))}
    rows = []
    for p in pairs:
        a, b = recs[p.feedback_a_id], recs[p.feedback_b_id]
        rows.append(
            {
                "pair": p.id,
                "Similarity": p.score,
                "Type": p.kind,
                "Review": p.review_status,
                "Original": a.text_original,
                "Original ID": a.id,
                "Original source": a.source,
                "Original date": a.created_at,
                "Related": b.text_original,
                "Related ID": b.id,
                "Related source": b.source,
                "Related date": b.created_at,
            }
        )
    df = pd.DataFrame(rows)
    ev = st.dataframe(
        df.drop(columns=["pair"]),
        hide_index=True,
        use_container_width=True,
        on_select="rerun",
        selection_mode="single-row",
        key=key,
        column_config={
            "Similarity": st.column_config.NumberColumn(format="%.3f"),
            "Original": st.column_config.TextColumn(width="medium"),
            "Related": st.column_config.TextColumn(width="medium"),
            "Original date": st.column_config.DatetimeColumn(format="YYYY-MM-DD"),
            "Related date": st.column_config.DatetimeColumn(format="YYYY-MM-DD"),
        },
    )
    rows_sel = ev.selection.rows if ev and ev.selection else []
    if rows_sel:
        row = df.iloc[rows_sel[0]]
        c1, c2, c3, c4 = st.columns(4)
        if c1.button("Confirm duplicate", key=f"{key}_c"):
            state.run_action(lambda ss: themes.review_pair(ss, int(row["pair"]), "confirmed"), "Marked as duplicate.")
        if c2.button("Dismiss", key=f"{key}_d"):
            state.run_action(lambda ss: themes.review_pair(ss, int(row["pair"]), "dismissed"), "Dismissed.")
        if c3.button("Open original", key=f"{key}_oa"):
            state.go("explorer", selected_feedback_id=row["Original ID"])
        if c4.button("Open related", key=f"{key}_ob"):
            state.go("explorer", selected_feedback_id=row["Related ID"])
        st.caption("Reviewing never merges or deletes records — both stay in the dataset.")


def _theme_list(summaries: list[dict]) -> int:
    """Clickable list of themes; returns the selected theme id."""
    ids = [x["id"] for x in summaries]
    current = st.session_state.get("selected_theme_id")
    if current not in ids:
        current = ids[0]
    query = st.text_input("Find a theme", placeholder="Search themes…", label_visibility="collapsed")
    sort = (
        st.segmented_control(
            "Sort by",
            ["Most mentioned", "Most unhappy", "Rising"],
            default="Most mentioned",
            key="theme_sort",
            label_visibility="collapsed",
        )
        or "Most mentioned"
    )
    shown = [x for x in summaries if query.lower() in x["label"].lower()] if query else list(summaries)
    if sort == "Most unhappy":
        shown.sort(key=lambda x: -x["neg"] / max(x["n"], 1))
    elif sort == "Rising":
        shown.sort(key=lambda x: (x["trend"] != "emerging", -x["n"]))
    with st.container(height=620, border=False):
        for x in shown:
            neg = round(100 * x["neg"] / max(x["n"], 1))
            arrow = "  :material/trending_up:" if x["trend"] == "emerging" else ""
            if st.button(
                f"{x['label']}{arrow}\n\n{x['n']} mentions · {neg}% unhappy",
                key=f"pick_{x['id']}",
                type="primary" if x["id"] == current else "secondary",
                use_container_width=True,
            ):
                st.session_state["selected_theme_id"] = x["id"]
                st.rerun()
        if not shown:
            st.caption("No theme matches your search.")
    return current


def _run_prompt() -> None:
    theme.next_step(
        "Find the problems in your feedback",
        "We'll group similar feedback into themes, score sentiment and spot rising issues. "
        "The first run downloads a small language model (~90 MB) and takes up to a minute.",
    )
    if st.button("Analyse feedback", type="primary") and state.analyze():
        st.session_state["flash"] = "Analysis complete."
        st.rerun()


def render() -> None:
    st.session_state["_page_key"] = "insights"
    theme.header(
        "Problem themes",
        "Similar feedback grouped into customer problems — each one backed by real quotes.",
        "Discover",
        "lightbulb",
    )
    with state.db().session() as s:
        if workspace.is_empty(s):
            no_data_notice()
            return
        run = themes.latest_run(s)
        if run is None:
            _run_prompt()
            _analysis_controls(run)
            return
        for note in (run.notes or "").split("\n"):
            if "fallback" in note.lower():
                st.warning(
                    "The language model wasn't available, so themes were found with a simpler keyword "
                    "method. Results may be less accurate. " + note
                )

        active = themes.active_themes(s)
        tab_themes, tab_dupes, tab_uncl = st.tabs(["Themes", "Similar requests", "Not grouped"])
        with tab_themes:
            if not active:
                theme.empty_state(
                    "No themes found yet",
                    "Try importing more feedback, or loosen the grouping in the advanced settings below.",
                )
            else:
                summaries = theme_summaries(active)
                left, right = st.columns([1, 2.6], gap="medium")
                with left:
                    st.caption(f"{len(active)} themes · {run.n_records:,} pieces of feedback grouped")
                    choice = _theme_list(summaries)
                with right, st.container(border=True):
                    _theme_detail(s, next(t for t in active if t.id == choice))
        with tab_dupes:
            kind = st.radio("Show", ["All", "Exact duplicates", "Semantic near-duplicates"], horizontal=True)
            status = st.multiselect(
                "Review status", ["unreviewed", "confirmed", "dismissed"], default=["unreviewed", "confirmed"]
            )
            min_score = st.slider(
                "Minimum similarity",
                float(run.params.get("similarity_threshold", 0.9)),
                1.0,
                float(run.params.get("similarity_threshold", 0.9)),
                0.01,
            )
            stmt = select(SimilarityPair).where(SimilarityPair.run_id == run.id, SimilarityPair.score >= min_score)
            if kind != "All":
                stmt = stmt.where(SimilarityPair.kind == ("exact" if kind.startswith("Exact") else "semantic"))
            if status:
                stmt = stmt.where(SimilarityPair.review_status.in_(status))
            pairs = list(s.scalars(stmt.order_by(SimilarityPair.score.desc()).limit(200)))
            st.caption(
                f"Showing {len(pairs)} pair(s) (max 200). Threshold used in this run: "
                f"{run.params.get('similarity_threshold')}."
            )
            if pairs:
                _pairs_table(s, pairs, key="dupes")
            else:
                st.caption("No pairs match.")
        with tab_uncl:
            member_ids = select(ThemeMembership.feedback_id).join(Theme).where(Theme.is_active.is_(True))
            uncl = list(
                s.scalars(
                    select(Feedback)
                    .where(Feedback.id.not_in(member_ids))
                    .order_by(Feedback.created_at.desc().nulls_last())
                    .limit(300)
                )
            )
            st.caption(
                f"{fb.count(s, fb.FeedbackFilter(theme_ids=[fb.UNTHEMED])):,} record(s) are not in any theme "
                "(too short, or not similar enough to a group). Showing up to 300."
            )
            if uncl:
                st.dataframe(
                    pd.DataFrame(
                        [
                            {
                                "ID": r.id,
                                "Feedback": r.text_original,
                                "Source": r.source,
                                "Date": r.created_at,
                                "Too short": r.analyzable is False,
                            }
                            for r in uncl
                        ]
                    ),
                    hide_index=True,
                    use_container_width=True,
                    column_config={"Feedback": st.column_config.TextColumn(width="large")},
                )

        st.write("")
        _analysis_controls(run)
