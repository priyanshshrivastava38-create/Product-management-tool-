"""Feedback Explorer — search, filter and inspect individual records."""

from __future__ import annotations

import html
import math

import streamlit as st

from productpulse.services import feedback as fb
from productpulse.services import themes, workspace
from productpulse.ui import state, theme
from productpulse.ui.views.common import no_data_notice

PAGE_SIZES = (25, 50, 100)


def _detail(s, feedback_id: str) -> None:
    rec = fb.get(s, feedback_id)
    if rec is None:
        st.warning(f"Record `{feedback_id}` was not found (it may have been deleted).")
        return
    st.markdown(f"#### Record `{rec.id}`")
    theme.badges("observed")
    st.caption("Original feedback (verbatim)")
    theme.quote(rec.text_original, f"{len(rec.text_original):,} characters as imported")
    meta = {
        "Source ID": rec.external_id or "—",
        "Date": rec.created_at.strftime("%Y-%m-%d %H:%M") if rec.created_at else "—",
        "Source": rec.source or "—",
        "Customer": rec.customer_id or "—",
        "Segment": rec.customer_segment or "—",
        "Product area": rec.product_area or "—",
        "Rating": f"{rec.rating:g}" if rec.rating is not None else "—",
        "Customer value": f"{rec.customer_value:,.0f}" if rec.customer_value is not None else "—",
        "Status": rec.status or "—",
        "Import batch": f"#{rec.import_batch_id}" if rec.import_batch_id else "—",
    }
    cols = st.columns(5)
    for i, (k, v) in enumerate(meta.items()):
        cols[i % 5].markdown(f"<div class='pp-meta'>{k}</div>{html.escape(str(v))}", unsafe_allow_html=True)
    st.write("")
    if rec.sentiment_label:
        st.markdown(
            theme.badge("calculated") + f" Sentiment **{rec.sentiment_label}** · VADER compound "
            f"{rec.sentiment_score:+.2f} · reliability **{rec.sentiment_reliability}**",
            unsafe_allow_html=True,
        )
    else:
        st.caption("Not analysed yet.")
    if rec.duplicate_of_id:
        st.caption(f"Exact-text duplicate of `{rec.duplicate_of_id}` (kept; flagged at import).")

    link = fb.links(s, rec.id)
    c1, c2 = st.columns(2)
    with c1:
        if link["theme"]:
            tid, label = link["theme"]
            if st.button(f"Open theme: {label}", key=f"theme_{rec.id}"):
                state.go("insights", selected_theme_id=tid)
        else:
            st.caption("Not part of an active theme.")
    with c2:
        for oid, name in link["opportunities"]:
            if st.button(f"Open opportunity: {name}", key=f"opp_{rec.id}_{oid}"):
                state.go("prioritization", selected_opportunity_id=oid, prio_view="Edit opportunity")
    rel = fb.related(s, rec.id)
    st.markdown("**Related feedback** (similarity from the latest analysis)")
    if not rel:
        st.caption("No duplicates or near-duplicates above the similarity threshold.")
    for other, score, kind in rel:
        with st.container(border=True):
            st.markdown(
                f"`{other.id}` · {kind} · similarity **{score:.2f}** · {other.source or '—'} · "
                f"{other.created_at:%Y-%m-%d}"
                if other.created_at
                else f"`{other.id}` · {kind} · {score:.2f}"
            )
            st.text(other.text_original[:600])
            if st.button("Inspect", key=f"rel_{rec.id}_{other.id}"):
                st.session_state["selected_feedback_id"] = other.id
                st.rerun()


def render() -> None:
    st.session_state["_page_key"] = "explorer"
    theme.header(
        "Feedback explorer",
        "Search every piece of feedback. Text and details are shown exactly as imported.",
        "Discover",
        "search",
    )
    qp_id = st.query_params.get("feedback")
    if qp_id:
        st.session_state["selected_feedback_id"] = qp_id

    with state.db().session() as s:
        if workspace.is_empty(s):
            no_data_notice()
            return
        facets = fb.facets(s)
        lo, hi = fb.date_bounds(s)
        active = themes.active_themes(s)
        theme_opts = {t.id: t.label for t in active}
        theme_opts[fb.UNTHEMED] = "— Not in a theme —"

        feel_labels = theme.FEELING_LABELS
        with st.container(border=True):
            c1, c2, c3 = st.columns([2.2, 1.4, 1])
            query = c1.text_input(
                "Search", placeholder="Keywords, record ID or customer ID (all terms must match)", key="explorer_query"
            )
            dates = c2.date_input(
                "Date range",
                value=(lo.date(), hi.date()) if lo else (),
                min_value=lo.date() if lo else None,
                max_value=hi.date() if hi else None,
            )
            sort = c3.selectbox("Sort", list(fb.SORTS))
            p1, p2 = st.columns([1, 1.6])
            sentiments = (
                p1.pills(
                    "Feeling", list(feel_labels), format_func=feel_labels.get, selection_mode="multi", key="ex_feel"
                )
                or []
            )
            segments = p2.pills("Segment", facets["segments"], selection_mode="multi", key="ex_seg") or []
            topics = [t.id for t in active[:8]]
            picked_topics = (
                st.pills("Popular topics", topics, format_func=theme_opts.get, selection_mode="multi", key="ex_topics")
                or []
            )
            f1, f3, f5 = st.columns(3)
            sources = f1.multiselect("Source", facets["sources"])
            areas = f3.multiselect("Product area", facets["product_areas"])
            theme_ids = f5.multiselect(
                "Theme",
                list(theme_opts),
                format_func=theme_opts.get,
                default=[st.session_state.pop("explorer_theme")] if "explorer_theme" in st.session_state else None,
            )
            include_undated = st.checkbox("Include records without a date", value=True)
            theme_ids = list(dict.fromkeys([*theme_ids, *picked_topics]))

        start, end = dates if isinstance(dates, tuple) and len(dates) == 2 else (None, None)
        flt = fb.FeedbackFilter(
            query=query,
            start=start,
            end=end,
            sources=sources,
            sentiments=sentiments,
            product_areas=areas,
            segments=segments,
            theme_ids=theme_ids,
            include_undated=include_undated,
        )
        page_size = st.session_state.get("explorer_page_size", 50)
        total = fb.count(s, flt)
        pages = max(1, math.ceil(total / page_size))
        p1, p2, p3 = st.columns([1, 1, 4])
        page = p1.number_input("Page", min_value=1, max_value=pages, value=1, step=1)
        p2.selectbox("Rows per page", PAGE_SIZES, index=PAGE_SIZES.index(page_size), key="explorer_page_size")
        p3.markdown(
            f"<div style='padding-top:2rem'>{total:,} matching record(s) · page {page} of {pages}</div>",
            unsafe_allow_html=True,
        )

        df, _ = fb.search(s, flt, page=int(page), page_size=page_size, sort=sort)
        if df.empty:
            theme.empty_state("No matching feedback", "Try removing a filter or searching for fewer words.")
            return
        event = st.dataframe(
            df,
            hide_index=True,
            use_container_width=True,
            on_select="rerun",
            selection_mode="single-row",
            key="explorer_table",
            column_config={
                "Feedback": st.column_config.TextColumn(width="large"),
                "Date": st.column_config.DatetimeColumn(format="YYYY-MM-DD"),
                "Score": st.column_config.NumberColumn(
                    format="%+.2f", help="VADER compound score (−1…1), not a probability"
                ),
            },
        )
        rows = event.selection.rows if event and event.selection else []
        if rows:
            st.session_state["selected_feedback_id"] = df.iloc[rows[0]]["ID"]
        st.caption("Select a row to see the full record, related feedback and linked insights.")
        selected = st.session_state.get("selected_feedback_id")
        if selected:
            with st.container(border=True):
                _detail(s, selected)
