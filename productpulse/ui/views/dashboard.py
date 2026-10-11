"""Home — what customers are saying, what's changing, and what to do next."""

from __future__ import annotations

import pandas as pd
import plotly.express as px
import streamlit as st

from productpulse.services import analytics, habits, prioritization, roadmap, themes, workspace
from productpulse.services import feedback as fb_service
from productpulse.ui import state, theme
from productpulse.ui.views.common import (
    filter_chips,
    load_sample_button,
    next_step_banner,
    theme_summaries,
    workflow_state,
)


def _welcome() -> None:
    theme.hero(
        "Turn customer feedback into your next best product decision",
        "Bring in support tickets, reviews, surveys and interview notes. ProductPulse groups them into "
        "customer problems, shows the real quotes behind each one, and helps you prioritise and plan — "
        "no API key needed.",
    )
    c1, c2, _ = st.columns([1.2, 1.2, 2.2])
    with c1:
        load_sample_button("dash_sample", "Try it with sample data")
    with c2:
        if st.button("Upload my own CSV", icon=":material/upload_file:", use_container_width=True):
            state.go("data")
    st.caption("The sample is ~1,400 synthetic feedback records for a fictional SaaS product — no real customer data.")
    st.write("")
    st.markdown("#### How it works")
    cols = st.columns(4)
    steps = [
        (
            "upload_file",
            "Import feedback",
            "Upload a CSV from any tool. Map your columns; bad rows are explained, never lost.",
        ),
        ("lightbulb", "Discover problems", "Similar feedback is grouped into themes, with sentiment and trends."),
        ("leaderboard", "Prioritise", "Turn a theme into an opportunity and score it with RICE — every input visible."),
        ("map", "Plan & share", "Place it on a Now / Next / Later roadmap and export a product brief."),
    ]
    for i, (col, (ic, t, d)) in enumerate(zip(cols, steps, strict=True), 1):
        with col:
            theme.step_card(i, t, d, ic)


PALETTE = {
    "emerging": "#E5484D",
    "stable": "#4F46E5",
    "declining": "#12A594",
    "insufficient_volume": "#A0AEC0",
    "insufficient_history": "#A0AEC0",
}


def _replay_figure(df: pd.DataFrame, summaries: list[dict]):
    """Animated bubble chart: cumulative mentions and unhappy share per problem, week by week."""
    info = {x["id"]: x for x in summaries}
    d = df[df["theme_id"].isin(info) & df["created_at"].notna()].copy()
    if d.empty:
        return None
    d["week"] = d["created_at"].dt.to_period("W").dt.start_time
    weeks = sorted(d["week"].unique())
    rows = []
    for w in weeks:
        upto = d[d["week"] <= w]
        g = upto.groupby("theme_id")
        counts, neg = g.size(), g["sentiment"].apply(lambda x: (x == "negative").sum())
        custs = g["customer_id"].nunique()
        for tid, x in info.items():
            n = int(counts.get(tid, 0))
            rows.append(
                {
                    "Week": pd.Timestamp(w).strftime("%b %d, %Y"),
                    "Problem": x["label"],
                    "Mentions": n,
                    "Unhappy %": round(100 * int(neg.get(tid, 0)) / n) if n else 0,
                    "Customers": max(int(custs.get(tid, 0)), 1) if n else 1,
                    "Trend": theme.TREND_LABELS.get(x["trend"] or "", "—"),
                }
            )
    data = pd.DataFrame(rows)
    colors = {theme.TREND_LABELS[k]: c for k, c in PALETTE.items()}
    fig = px.scatter(
        data,
        x="Mentions",
        y="Unhappy %",
        size="Customers",
        color="Trend",
        hover_name="Problem",
        animation_frame="Week",
        animation_group="Problem",
        size_max=48,
        color_discrete_map=colors,
        range_x=[-5, data["Mentions"].max() * 1.12],
        range_y=[-5, 105],
    )
    fig.update_traces(marker=dict(opacity=0.78, line_width=1))
    fig.update_layout(
        height=470,
        legend_title=None,
        legend=dict(orientation="h", y=1.1),
        yaxis=dict(ticksuffix="%"),
        margin=dict(l=8, r=8, t=50, b=8),
    )
    if fig.layout.updatemenus:
        play = fig.layout.updatemenus[0].buttons[0].args[1]
        play["frame"]["duration"] = 260
        play["transition"]["duration"] = 220
        play["transition"]["easing"] = "cubic-in-out"
    if fig.layout.sliders:
        fig.layout.sliders[0].currentvalue.prefix = "Week of "
    return fig


def _problem_map(summaries: list[dict], df: pd.DataFrame | None = None) -> None:
    """Bubble chart of every problem: click a bubble to open it, or replay how problems grew."""
    if not summaries:
        return
    head, toggle = st.columns([3, 1.3], vertical_alignment="bottom")
    head.markdown("#### Problem map")
    mode = (
        toggle.segmented_control(
            "Map mode",
            [":material/touch_app: Today", ":material/play_circle: Replay"],
            default=":material/touch_app: Today",
            key="map_mode",
            label_visibility="collapsed",
        )
        or ":material/touch_app: Today"
    )
    if "Replay" in mode and df is not None:
        st.caption("Press **▶ Play** to watch each problem grow week by week, or drag the slider to any week.")
        fig = _replay_figure(df, summaries)
        if fig is None:
            st.info("Replay needs dated feedback.")
        else:
            st.plotly_chart(fig, use_container_width=True, key="problem_replay")
        return
    st.caption(
        "Bigger bubble = more customers · higher = more unhappy · further right = mentioned more. "
        "**Click a bubble** to open that problem."
    )
    data = pd.DataFrame(
        [
            {
                "id": x["id"],
                "Problem": x["label"],
                "Mentions": x["n"],
                "Customers": max(x["customers"], 1),
                "Unhappy %": round(100 * x["neg"] / max(x["n"], 1)),
                "Trend": theme.TREND_LABELS.get(x["trend"] or "", "—"),
            }
            for x in summaries
        ]
    )
    colors = {theme.TREND_LABELS[k]: c for k, c in PALETTE.items()}
    biggest = set(data.nlargest(6, "Mentions")["id"])
    data["Label"] = [p if i in biggest else "" for p, i in zip(data["Problem"], data["id"], strict=True)]
    fig = px.scatter(
        data,
        x="Mentions",
        y="Unhappy %",
        size="Customers",
        color="Trend",
        text="Label",
        hover_name="Problem",
        custom_data=["id"],
        size_max=48,
        color_discrete_map=colors,
        hover_data={"Customers": True, "id": False, "Trend": True, "Label": False},
    )
    fig.update_traces(textposition="top center", textfont_size=11, marker=dict(opacity=0.75, line_width=1))
    fig.update_layout(
        height=430,
        legend_title=None,
        legend=dict(orientation="h", y=1.08),
        yaxis=dict(range=[-5, 105], ticksuffix="%"),
        clickmode="event+select",
    )
    event = st.plotly_chart(
        fig, use_container_width=True, on_select="rerun", selection_mode="points", key="problem_map"
    )
    points = event.selection.points if event and event.selection else []
    if points and points[0].get("customdata"):
        state.go("insights", selected_theme_id=int(points[0]["customdata"][0]))


def _previous_visit():
    """Previous visit time, read once per browser session (then the visit is recorded)."""
    if "prev_visit" not in st.session_state:
        with state.db().session() as w:
            st.session_state["prev_visit"] = habits.touch_visit(w)
    return st.session_state["prev_visit"]


@st.dialog("Weekly summary", width="large")
def _summary_dialog() -> None:
    with state.db().session() as s:
        text = habits.weekly_summary(s)
    st.caption("Copy it with the button in the top-right corner of the box, or download it.")
    st.code(text, language="markdown", wrap_lines=True)
    st.download_button(
        "Download as Markdown", text.encode(), "weekly_summary.md", "text/markdown", icon=":material/download:"
    )


def _since_last_visit(s, prev_visit) -> None:
    d = habits.since(s, prev_visit)
    items = []
    if prev_visit is not None:
        n_new = d["new_feedback"]
        items.append(
            (
                "forum",
                f"{n_new:,} new pieces of feedback" if n_new else "No new feedback yet",
                f"since your last visit on {prev_visit:%b %d, %H:%M} UTC"
                + ("" if n_new else " — click “Add feedback” to import this week's export"),
            )
        )
    for t in d["rising"][:2]:
        items.append(
            (
                "local_fire_department",
                f"{t.label} is rising fast",
                f"{t.trend.get('recent')} mentions in the last {t.trend.get('window_days')} days",
            )
        )
    for label, n in d["new_themes"][:2]:
        items.append(("fiber_new", f"New problem: {label}", f"{n} mentions"))
    for label, before, now in d["grown"][:2]:
        items.append(("trending_up", f"{label} grew", f"{before} → {now} mentions since the previous analysis"))
    if not items:
        return
    title = "Since your last visit" if prev_visit else "What needs your attention"
    st.markdown(f"#### {title}")
    theme.digest(items[:6])
    st.write("")


def _watchlist(s) -> None:
    rows = habits.watched(s)
    st.markdown("#### Watching")
    if not rows:
        st.caption(
            "Follow the problems you care about — open a theme and click **Watch**. "
            "They'll appear here with their latest numbers every time you come back."
        )
        st.write("")
        return
    cols = st.columns(min(len(rows), 3))
    for i, row in enumerate(rows):
        with cols[i % len(cols)]:
            t = row["theme"]
            if t is None:
                theme.card(row["entry"]["label"], "—", "No longer found after re-analysis", "visibility_off")
                if st.button("Stop watching", key=f"unw_{i}", use_container_width=True):
                    state.run_action(lambda ss, lb=row["entry"]["label"]: habits.unwatch(ss, lb), "Removed.")
                continue
            n, start = len(t.memberships), row["entry"]["start_n"]
            delta = n - start
            neg = sum(m.feedback.sentiment_label == "negative" for m in t.memberships)
            change = "no change" if delta == 0 else f"{'+' if delta > 0 else ''}{delta} since you started watching"
            theme.card(
                t.label,
                f"{n} mentions",
                f"{change} · {theme.pct(neg, n)} unhappy",
                theme.TREND_ICON.get((t.trend or {}).get("status"), "visibility"),
                "amber" if (t.trend or {}).get("status") == "emerging" else "",
            )
            if st.button("Open", key=f"wopen_{t.id}", use_container_width=True):
                state.go("insights", selected_theme_id=t.id)
    st.write("")


def render() -> None:
    st.session_state["_page_key"] = "dashboard"
    with state.db().session() as s:
        if workspace.is_empty(s):
            _welcome()
            return

        wf = workflow_state(s)
        prev_visit = _previous_visit()
        head, actions = st.columns([2.2, 1.6], vertical_alignment="bottom")
        with head:
            greeting = "Welcome back" if prev_visit else "Home"
            theme.header(greeting, "Here's what customers are telling you right now.", "Overview", "space_dashboard")
        with actions:
            a1, a2 = st.columns(2)
            if a1.button(
                "Add feedback",
                icon=":material/upload_file:",
                use_container_width=True,
                help="Import this week's export — your column mapping is remembered.",
            ):
                state.go("data")
            if a2.button(
                "Weekly summary",
                icon=":material/summarize:",
                use_container_width=True,
                disabled=not wf["analysed"],
                help="A ready-to-paste update for Slack or email.",
            ):
                _summary_dialog()
        if not all(wf.values()):
            next_step_banner(wf, "home")
            st.write("")
        if wf["analysed"]:
            _since_last_visit(s, prev_visit)
            _watchlist(s)

        k = analytics.kpis(s)
        df = fb_service.all_feedback_frame(s)
        st.markdown("##### Focus on")
        allowed = filter_chips(df, "home")
        view = df if allowed is None else df[df["id"].isin(allowed)]
        if allowed is not None and view.empty:
            st.warning("No feedback matches these filters — click a chip again to clear it.")
            return
        scored = int(view["sentiment"].notna().sum())
        negative = int((view["sentiment"] == "negative").sum())
        dated = view["created_at"].dropna()
        period = f"{dated.min():%b %d} – {dated.max():%b %d, %Y}" if len(dated) else "no dates"
        active = themes.active_themes(s) if wf["analysed"] else []
        summaries = theme_summaries(active, allowed)
        opps = k["opportunities"]
        theme.cards(
            [
                ("Feedback", f"{len(view):,}", period if allowed is None else f"filtered from {len(df):,}", "forum"),
                ("Unhappy", theme.pct(negative, scored), "of feedback is negative", "sentiment_dissatisfied", "red"),
                ("Problems found", str(len(summaries)), "groups of similar feedback", "lightbulb"),
                (
                    "Rising fast",
                    str(sum(x["trend"] == "emerging" for x in summaries)),
                    "growing vs. before",
                    "trending_up",
                    "amber",
                ),
                (
                    "On roadmap",
                    str(k["roadmap_items"]),
                    f"{opps} opportunit{'y' if opps == 1 else 'ies'} scored",
                    "map",
                    "green",
                ),
            ]
        )
        st.write("")

        if not wf["analysed"]:
            return
        _problem_map(summaries, view)

        rising = [x for x in summaries if x["trend"] == "emerging"]
        if rising:
            st.markdown("#### :material/trending_up: Rising fast")
            if allowed is not None:
                st.caption("Counts reflect your filters; “rising” is measured across all feedback.")
            cols = st.columns(min(len(rising), 3))
            for i, (col, x) in enumerate(zip(cols, rising[:3], strict=False)):
                with col:
                    theme.theme_card(
                        x["label"],
                        x["example"],
                        x["n"],
                        x["customers"],
                        x["neg"],
                        x["neu"],
                        x["pos"],
                        x["trend"],
                        delay_ms=90 * i,
                    )
                    if st.button("See the evidence", key=f"rise_{x['id']}", use_container_width=True):
                        state.go("insights", selected_theme_id=x["id"])
            st.write("")

        head, link = st.columns([4, 1])
        head.markdown("#### Biggest customer problems")
        if link.button("View all themes →", use_container_width=True):
            state.go("insights")
        if not summaries:
            st.info("The analysis didn't find clear themes yet. Try importing more feedback.")
        rising_ids = {x["id"] for x in rising[:3]}
        top = [x for x in summaries if x["id"] not in rising_ids][:6]
        for row_start in range(0, len(top), 3):
            cols = st.columns(3)
            for j, (col, x) in enumerate(zip(cols, top[row_start : row_start + 3], strict=False)):
                with col:
                    theme.theme_card(
                        x["label"],
                        x["example"],
                        x["n"],
                        x["customers"],
                        x["neg"],
                        x["neu"],
                        x["pos"],
                        x["trend"],
                        delay_ms=70 * (row_start + j),
                    )
                    if st.button("Open", key=f"top_{x['id']}", use_container_width=True):
                        state.go("insights", selected_theme_id=x["id"])
        st.write("")

        left, right = st.columns([1.3, 1])
        with left:
            st.markdown("#### How customers feel over time")
            if analytics.has_history(k["span_days"]):
                vol = analytics.volume_over_time(view, "sentiment")
                fig = px.area(
                    vol,
                    x="period",
                    y="count",
                    color="sentiment",
                    color_discrete_map=theme.SENTIMENT_COLORS,
                    category_orders={"sentiment": ["negative", "neutral", "positive"]},
                )
                fig.update_layout(
                    height=280,
                    xaxis_title=None,
                    yaxis_title="Feedback per week",
                    legend_title=None,
                    legend=dict(orientation="h", y=1.12),
                )
                st.plotly_chart(fig, use_container_width=True)
            else:
                st.caption(
                    f"Your data covers {k['span_days']} days — add at least "
                    f"{analytics.MIN_HISTORY_DAYS} days of feedback to see trends."
                )
        with right:
            st.markdown("#### Top opportunities")
            rank = prioritization.ranking_frame(prioritization.list_opportunities(s))
            if rank.empty:
                st.caption("None yet — open a problem theme and click “Create opportunity”.")
            else:
                for _, r in rank.head(5).iterrows():
                    st.markdown(
                        f"**#{r['Rank']} {r['Opportunity']}**  \nRICE {r['RICE']:,.1f} · {r['Status']} · "
                        f"{r['Evidence']} customer quotes"
                    )
            board = roadmap.board_frame(s)
            now = board[board["Horizon"] == "Now"] if not board.empty else board
            st.markdown("#### Building now")
            if now.empty:
                st.caption("Nothing in **Now** yet.")
            for _, r in now.iterrows():
                st.markdown(f"• **{r['Initiative']}** — {r['Status']}")

        run = themes.latest_run(s)
        with st.expander("About this analysis"):
            st.caption(
                f"Last analysed {run.finished_at:%Y-%m-%d %H:%M} UTC (run #{run.id}) using "
                f"{run.embedding_method} + {run.clustering_method} clustering. "
                "Every theme links back to the exact customer feedback behind it — see “How it works”."
            )
