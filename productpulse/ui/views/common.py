"""Widgets shared by several pages."""

from __future__ import annotations

import streamlit as st

from productpulse.services import workspace
from productpulse.ui import state


def load_sample_button(key: str, label: str = "Load sample workspace", primary: bool = True) -> None:
    if st.button(
        label,
        key=key,
        type="primary" if primary else "secondary",
        icon=":material/play_circle:",
        use_container_width=True,
        help="Imports ~1,400 clearly labelled synthetic feedback records and runs the analysis.",
    ):
        summary = state.run_action(lambda s: workspace.load_sample(s, state.settings()), rerun=False)
        if summary is None:
            return
        run = state.analyze()
        if run is not None:
            st.session_state["flash"] = (
                f"Sample workspace loaded: {summary.inserted:,} new records, " f"{run.n_themes} themes discovered."
            )
            st.rerun()


def no_data_notice() -> None:
    st.info("There is no feedback yet. Load the sample workspace or upload a CSV in **Data Management**.")
    c1, c2, _ = st.columns([1, 1, 3])
    with c1:
        load_sample_button("sample_" + st.session_state.get("_page_key", "x"))
    with c2:
        if st.button("Upload a CSV", key="goto_upload_" + st.session_state.get("_page_key", "x")):
            state.go("data")


def no_analysis_notice() -> None:
    st.info("Feedback is loaded but has not been analysed yet. Run the analysis on the **AI Insights** page.")
    if st.button("Go to AI Insights", key="goto_insights_" + st.session_state.get("_page_key", "x")):
        state.go("insights")


# ----------------------------------------------------------------- guided workflow


def workflow_state(session) -> dict[str, bool]:
    from productpulse.services import analytics
    from productpulse.services import themes as theme_service

    k = analytics.kpis(session)
    return {
        "data": k["total"] > 0,
        "analysed": theme_service.latest_run(session) is not None,
        "opportunity": k["opportunities"] > 0,
        "roadmap": k["roadmap_items"] > 0,
    }


STEPS = [
    ("data", "Import feedback"),
    ("analysed", "Discover problems"),
    ("opportunity", "Prioritise an opportunity"),
    ("roadmap", "Plan the roadmap"),
]


def next_step_banner(wf: dict[str, bool], key: str) -> None:
    """One clear call to action based on where the user is in the journey."""
    from productpulse.ui import theme

    if not wf["data"]:
        return
    if not wf["analysed"]:
        theme.next_step(
            "Find the problems in your feedback",
            "Group similar feedback into themes and score sentiment. Takes under a minute.",
        )
        if st.button("Analyse feedback", type="primary", key=f"{key}_analyse", icon=":material/auto_awesome:") and (
            state.analyze()
        ):
            st.session_state["flash"] = "Analysis complete."
            st.rerun()
    elif not wf["opportunity"]:
        theme.next_step(
            "Turn a top problem into an opportunity",
            "Open a theme, check the customer quotes, then click “Create opportunity”.",
        )
        if st.button("Explore problem themes", type="primary", key=f"{key}_themes", icon=":material/lightbulb:"):
            state.go("insights")
    elif not wf["roadmap"]:
        theme.next_step(
            "Put your best opportunity on the roadmap",
            "Pick what goes in Now, Next or Later and export a product brief to share.",
        )
        if st.button("Open the roadmap", type="primary", key=f"{key}_road", icon=":material/map:"):
            state.go("roadmap")
    else:
        theme.next_step(
            "Share your plan", "Export a product brief backed by customer quotes, or save a roadmap version."
        )
        if st.button("Export a product brief", key=f"{key}_brief", icon=":material/ios_share:"):
            state.go("roadmap")


def theme_summaries(themes_list, allowed_ids: set[str] | None = None) -> list[dict]:
    """Per-theme counts for cards and charts; ``allowed_ids`` restricts to a filtered subset of feedback."""
    from productpulse.services import themes as theme_service

    out = []
    for t in themes_list:
        members = [m.feedback for m in t.memberships if allowed_ids is None or m.feedback_id in allowed_ids]
        if not members:
            continue
        rep = theme_service.representative_members(t, 1)
        out.append(
            {
                "theme": t,
                "id": t.id,
                "label": t.label,
                "n": len(members),
                "customers": len({m.customer_id for m in members if m.customer_id}),
                "neg": sum(m.sentiment_label == "negative" for m in members),
                "neu": sum(m.sentiment_label == "neutral" for m in members),
                "pos": sum(m.sentiment_label == "positive" for m in members),
                "example": next(
                    (
                        m.feedback.text_original
                        for m in sorted(t.memberships, key=lambda m: -m.centroid_similarity)
                        if allowed_ids is None or m.feedback_id in allowed_ids
                    ),
                    rep[0].text_original if rep else "",
                ),
                "trend": (t.trend or {}).get("status"),
            }
        )
    return out


def filter_chips(df, key: str) -> set[str] | None:
    """Clickable segment/source chips. Returns the matching feedback IDs, or None when unfiltered."""
    segs = sorted(x for x in df["segment"].dropna().unique())
    srcs = sorted(x for x in df["source"].dropna().unique())
    c1, c2 = st.columns([1, 1.6])
    with c1:
        seg = st.pills("Customer segment", segs, selection_mode="multi", key=f"{key}_seg")
    with c2:
        src = st.pills("Feedback source", srcs, selection_mode="multi", key=f"{key}_src")
    if not seg and not src:
        return None
    view = df
    if seg:
        view = view[view["segment"].isin(seg)]
    if src:
        view = view[view["source"].isin(src)]
    return set(view["id"])
