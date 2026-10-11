"""Trends and Analytics — volume, sentiment, theme dynamics, priority and roadmap history."""

from __future__ import annotations

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st
from sqlalchemy import select

from productpulse.db.models import AnalysisRun
from productpulse.services import analytics, audit, prioritization, themes, workspace
from productpulse.services import feedback as fb
from productpulse.ui import state, theme
from productpulse.ui.views.common import no_data_notice


def _sankey(links: pd.DataFrame) -> None:
    links = links.fillna({"theme": "Manual idea (no theme)", "horizon": "Not on roadmap"})
    nodes = list(dict.fromkeys(list(links["theme"]) + list(links["opportunity"]) + list(links["horizon"])))
    idx = {n: i for i, n in enumerate(nodes)}
    src, tgt, val = [], [], []
    for _, r in links.iterrows():
        w = max(int(r["evidence"]), 1)
        src += [idx[r["theme"]], idx[r["opportunity"]]]
        tgt += [idx[r["opportunity"]], idx[r["horizon"]]]
        val += [w, w]
    fig = go.Figure(
        go.Sankey(
            node=dict(label=nodes, pad=14, thickness=14, color="#C7D2FE"),
            link=dict(source=src, target=tgt, value=val, color="rgba(79,70,229,0.18)"),
        )
    )
    fig.update_layout(
        height=max(300, 28 * len(nodes)), title="Theme → opportunity → roadmap (width = evidence records)"
    )
    st.plotly_chart(fig, use_container_width=True)


def render() -> None:
    st.session_state["_page_key"] = "analytics"
    theme.header(
        "Trends",
        "How feedback, sentiment and priorities change over time — computed from your data, never estimated.",
        "Discover",
        "monitoring",
    )
    with state.db().session() as s:
        if workspace.is_empty(s):
            no_data_notice()
            return
        df = fb.all_feedback_frame(s)
        facets = fb.facets(s)
        lo, hi = fb.date_bounds(s)
        with st.container(border=True):
            c1, c2, c3, c4 = st.columns([1.4, 1, 1, 1])
            dates = c1.date_input(
                "Date range",
                value=(lo.date(), hi.date()) if lo else (),
                min_value=lo.date() if lo else None,
                max_value=hi.date() if hi else None,
            )
            sources = c2.multiselect("Source", facets["sources"])
            segments = c3.multiselect("Segment", facets["segments"])
            freq = c4.selectbox("Granularity", ["Week", "Month"])
        view = df
        if isinstance(dates, tuple) and len(dates) == 2:
            mask = df["created_at"].isna() | (
                (df["created_at"].dt.date >= dates[0]) & (df["created_at"].dt.date <= dates[1])
            )
            view = view[mask]
        if sources:
            view = view[view["source"].isin(sources)]
        if segments:
            view = view[view["segment"].isin(segments)]
        dated = view.dropna(subset=["created_at"])
        span = (dated["created_at"].max() - dated["created_at"].min()).days if len(dated) > 1 else 0
        neg = (view["sentiment"] == "negative").sum()
        scored = view["sentiment"].notna().sum()
        theme.cards(
            [
                ("Feedback in view", f"{len(view):,}", f"{len(view) - len(dated):,} undated", "forum"),
                ("Negative", theme.pct(neg, scored), f"{neg:,} of {scored:,} scored", "sentiment_dissatisfied", "red"),
                (
                    "Positive",
                    theme.pct((view["sentiment"] == "positive").sum(), scored),
                    "of scored feedback",
                    "sentiment_satisfied",
                    "green",
                ),
                (
                    "History",
                    f"{span} days",
                    "enough for trends"
                    if analytics.has_history(span)
                    else f"need ≥ {analytics.MIN_HISTORY_DAYS} days for trends",
                    "calendar_month",
                ),
            ]
        )
        st.write("")
        rule = "W-MON" if freq == "Week" else "M"

        tabs = st.tabs(["Volume & sentiment", "Themes", "Priorities & roadmap", "Analysis history", "Audit log"])
        with tabs[0]:
            if dated.empty:
                st.info("No dated records in this view, so time series are unavailable.")
            else:
                by = st.radio(
                    "Break down by",
                    ["sentiment", "source", "segment", "product_area"],
                    horizontal=True,
                    format_func=lambda x: x.replace("_", " ").title(),
                )
                vol = analytics.volume_over_time(dated, by, rule)
                fig = px.bar(
                    vol,
                    x="period",
                    y="count",
                    color=by,
                    color_discrete_map=theme.SENTIMENT_COLORS if by == "sentiment" else None,
                    title=f"New feedback per {freq.lower()}",
                )
                fig.update_layout(height=340, xaxis_title=None, yaxis_title="Records", legend_title=None)
                st.plotly_chart(fig, use_container_width=True)
                if not analytics.has_history(span):
                    st.caption(":material/info: Short history: week-to-week variation may be noise.")
            dims = st.columns(3)
            for col, dim, title in zip(
                dims, ["source", "product_area", "segment"], ["Source", "Product area", "Segment"], strict=True
            ):
                with col:
                    d = view.dropna(subset=["sentiment"]).fillna({dim: "Unknown"})
                    if d.empty:
                        continue
                    g = d.groupby([dim, "sentiment"]).size().reset_index(name="n")
                    g["share"] = g["n"] / g.groupby(dim)["n"].transform("sum")
                    fig = px.bar(
                        g,
                        x="share",
                        y=dim,
                        color="sentiment",
                        orientation="h",
                        title=f"Sentiment by {title.lower()}",
                        color_discrete_map=theme.SENTIMENT_COLORS,
                        hover_data=["n"],
                        category_orders={"sentiment": ["negative", "neutral", "positive"]},
                    )
                    fig.update_layout(
                        height=320, xaxis_tickformat=".0%", yaxis_title=None, xaxis_title=None, showlegend=False
                    )
                    st.plotly_chart(fig, use_container_width=True)
        with tabs[1]:
            active = themes.active_themes(s)
            if not active:
                st.info("Run the analysis to see theme analytics.")
            else:
                tv = view.dropna(subset=["theme"])
                top = tv["theme"].value_counts().head(12).reset_index()
                top.columns = ["Theme", "Records"]
                fig = px.bar(
                    top.iloc[::-1],
                    x="Records",
                    y="Theme",
                    orientation="h",
                    title="Most frequent themes (current filters)",
                )
                fig.update_layout(height=380, yaxis_title=None)
                st.plotly_chart(fig, use_container_width=True)
                changes = analytics.theme_changes(active)
                st.markdown(
                    "**Largest changes in frequency** (comparison window vs baseline, from the latest analysis)"
                )
                st.dataframe(
                    changes.drop(columns=["theme_id"]),
                    hide_index=True,
                    use_container_width=True,
                    column_config={
                        "Explanation": st.column_config.TextColumn(width="large"),
                        "Change": st.column_config.NumberColumn(format="%+.1f"),
                    },
                )
                if not dated.empty and analytics.has_history(span):
                    pick = st.multiselect("Theme volume over time", list(top["Theme"]), default=list(top["Theme"][:4]))
                    if pick:
                        tvol = analytics.volume_over_time(tv[tv["theme"].isin(pick)], "theme", rule)
                        fig = px.line(tvol, x="period", y="count", color="theme", markers=True)
                        fig.update_layout(height=320, xaxis_title=None, legend_title=None)
                        st.plotly_chart(fig, use_container_width=True)
        with tabs[2]:
            hist = prioritization.score_history(s)
            if hist.empty:
                st.caption("No priority history yet — create opportunities to start tracking.")
            else:
                fig = px.line(
                    hist,
                    x="recorded_at",
                    y="score",
                    color="opportunity",
                    markers=True,
                    line_shape="hv",
                    hover_data=["reason"],
                    title="Priority (RICE) score changes",
                )
                fig.update_layout(height=340, xaxis_title=None, legend_title=None)
                st.plotly_chart(fig, use_container_width=True)
            links = analytics.traceability_links(s)
            if not links.empty:
                _sankey(links)
            k = analytics.kpis(s)
            st.markdown("**Feedback-to-opportunity traceability**")
            st.dataframe(
                pd.DataFrame(
                    [
                        {
                            "Metric": "Feedback records in an active theme",
                            "Value": f"{k['in_theme']:,} / {k['total']:,}",
                        },
                        {
                            "Metric": "Active themes linked to an opportunity",
                            "Value": f"{k['themes_linked']} / {k['themes']}",
                        },
                        {
                            "Metric": "Opportunities with linked evidence",
                            "Value": f"{k['opps_with_evidence']} / {k['opportunities']}",
                        },
                        {"Metric": "Distinct feedback records cited as evidence", "Value": f"{k['feedback_linked']:,}"},
                        {"Metric": "Roadmap initiatives", "Value": str(k["roadmap_items"])},
                    ]
                ),
                hide_index=True,
                use_container_width=True,
            )
            road = audit.list_events(
                s, ["roadmap_added", "roadmap_updated", "roadmap_reordered", "roadmap_removed", "roadmap_snapshot"]
            )
            st.markdown("**Roadmap changes over time**")
            if road:
                rdf = pd.DataFrame(
                    [
                        {"When": e.created_at, "Event": e.event_type.replace("roadmap_", ""), "Summary": e.summary}
                        for e in road
                    ]
                )
                cnt = rdf.assign(day=rdf["When"].dt.floor("D")).groupby(["day", "Event"]).size().reset_index(name="n")
                fig = px.bar(cnt, x="day", y="n", color="Event")
                fig.update_layout(height=240, xaxis_title=None, yaxis_title="Changes", legend_title=None)
                st.plotly_chart(fig, use_container_width=True)
                st.dataframe(rdf, hide_index=True, use_container_width=True)
            else:
                st.caption("No roadmap changes recorded yet.")
        with tabs[3]:
            runs = analytics.runs_frame(s)
            if runs.empty:
                st.caption("No analysis runs yet.")
            else:
                st.dataframe(runs, hide_index=True, use_container_width=True)
                done = list(
                    s.scalars(
                        select(AnalysisRun).where(AnalysisRun.status == "completed").order_by(AnalysisRun.id.desc())
                    )
                )
                if len(done) >= 2:
                    st.markdown("**Compare two analysis snapshots** (theme sizes are recorded at run time)")
                    a, b = st.columns(2)
                    ra = a.selectbox("Earlier run", [r.id for r in done], index=1)
                    rb = b.selectbox("Later run", [r.id for r in done], index=0)
                    cmp = analytics.compare_runs(
                        next(r for r in done if r.id == ra), next(r for r in done if r.id == rb)
                    )
                    st.dataframe(cmp, hide_index=True, use_container_width=True)
                    st.caption("Themes are matched by name; renamed or re-clustered themes appear as separate rows.")
                else:
                    st.caption("Run the analysis again after importing new data to compare snapshots.")
        with tabs[4]:
            events = audit.list_events(s, limit=500)
            st.dataframe(
                pd.DataFrame(
                    [
                        {
                            "When": e.created_at,
                            "Type": e.event_type,
                            "Entity": f"{e.entity_type} {e.entity_id}",
                            "Summary": e.summary,
                        }
                        for e in events
                    ]
                ),
                hide_index=True,
                use_container_width=True,
                column_config={"Summary": st.column_config.TextColumn(width="large")},
            )
