"""Feature Prioritization — opportunities, transparent RICE scoring, sensitivity and scenarios."""

from __future__ import annotations

import pandas as pd
import plotly.express as px
import streamlit as st
from pydantic import ValidationError

from productpulse.db.models import Feedback
from productpulse.services import prioritization as pz
from productpulse.ui import state, theme

NEW = -1


def _opportunity_editor(s, opps) -> None:
    ids = [NEW] + [o.id for o in opps]
    names = {NEW: "+  New opportunity", **{o.id: o.name for o in opps}}
    current = st.session_state.get("selected_opportunity_id", ids[1] if len(ids) > 1 else NEW)
    if current not in ids:
        current = NEW
    oid = st.selectbox("Opportunity", ids, index=ids.index(current), format_func=names.get)
    st.session_state["selected_opportunity_id"] = oid
    o = next((x for x in opps if x.id == oid), None)
    k = f"o{oid}"

    c1, c2 = st.columns([1.3, 1])
    with c1:
        name = st.text_input("Name", value=o.name if o else "", key=f"{k}_name")
        problem = st.text_area("Problem statement", value=o.problem_statement if o else "", key=f"{k}_prob", height=100)
        solution = st.text_area("Proposed solution", value=o.proposed_solution if o else "", key=f"{k}_sol", height=80)
        rationale = st.text_area(
            "Business rationale", value=o.business_rationale if o else "", key=f"{k}_rat", height=80
        )
        m1, m2, m3 = st.columns(3)
        owner = m1.text_input("Owner", value=o.owner if o else "", key=f"{k}_own")
        status = m2.selectbox("Status", pz.STATUSES, index=pz.STATUSES.index(o.status) if o else 0, key=f"{k}_st")
        target = m3.text_input("Target release / timeframe", value=o.target_release if o else "", key=f"{k}_tgt")
        m4, m5 = st.columns(2)
        area = m4.text_input("Product area", value=o.product_area if o else "", key=f"{k}_area")
        segment = m5.text_input("Segment", value=o.segment if o else "", key=f"{k}_seg")
    with c2:
        st.markdown(theme.badge("assumption") + " **RICE inputs**", unsafe_allow_html=True)
        reach = st.number_input(
            "Reach (customers or accounts per quarter)",
            min_value=0.0,
            step=1.0,
            value=float(o.reach) if o else 0.0,
            key=f"{k}_reach",
        )
        is_proxy = st.checkbox("Reach is a proxy estimate", value=o.reach_is_proxy if o else False, key=f"{k}_proxy")
        basis = st.text_area("How reach was estimated", value=o.reach_basis if o else "", key=f"{k}_basis", height=90)
        impact = st.select_slider(
            "Impact",
            options=list(pz.IMPACT_SCALE),
            value=o.impact if o else 1.0,
            format_func=lambda v: f"{v:g} · {pz.IMPACT_SCALE[v]}",
            key=f"{k}_imp",
        )
        conf = st.slider(
            "Confidence (%)",
            10,
            100,
            int(round((o.confidence if o else 0.8) * 100)),
            5,
            key=f"{k}_conf",
            help="Anchors: 50% low, 80% medium, 100% high.",
        )
        effort = st.number_input(
            "Effort (person-months, > 0)", min_value=0.1, step=0.5, value=float(o.effort) if o else 1.0, key=f"{k}_eff"
        )
        try:
            rice = pz.RiceInputs(reach=reach, impact=impact, confidence=conf, effort=effort)
            st.markdown(theme.badge("calculated") + " **RICE score**", unsafe_allow_html=True)
            st.markdown(
                f"<div class='pp-card'><div class='val'>{pz.rice_score(rice):,.2f}</div>"
                f"<div class='hint'>{pz.formula_text(rice)}</div></div>",
                unsafe_allow_html=True,
            )
            if o:
                live = pz.ranking_frame(opps, {str(o.id): rice.model_dump()}).set_index("id")
                saved = pz.ranking_frame(opps).set_index("id")
                now_rank, was_rank = int(live.loc[o.id, "Rank"]), int(saved.loc[o.id, "Rank"])
                move = (
                    ""
                    if now_rank == was_rank
                    else (" :material/arrow_upward:" if now_rank < was_rank else " :material/arrow_downward:")
                )
                st.markdown(
                    f"**Would rank #{now_rank} of {len(opps)}**{move}"
                    + (f" (currently #{was_rank})" if now_rank != was_rank else "")
                )
                if abs(pz.rice_score(rice) - pz.score_of(o)) > 1e-9:
                    st.caption(f"Saved score: {pz.score_of(o):,.2f} — unsaved changes, click Save to keep them.")
        except ValidationError as exc:
            st.error(state.format_validation(exc))
        if is_proxy:
            st.caption(
                ":material/info: Reach is an estimate from feedback — "
                "feedback counts are not the number of customers affected."
            )

    try:
        data = pz.OpportunityInput(
            name=name,
            problem_statement=problem,
            proposed_solution=solution,
            business_rationale=rationale,
            owner=owner,
            status=status,
            target_release=target,
            product_area=area,
            segment=segment,
            reach=reach,
            reach_is_proxy=is_proxy,
            reach_basis=basis,
            impact=impact,
            confidence=conf,
            effort=effort,
        )
    except ValidationError as exc:
        data = None
        if name:
            st.error(state.format_validation(exc))
    b1, _, b2, b3 = st.columns([1.2, 2.2, 1, 0.8], vertical_alignment="center")
    if o is None:
        if b1.button("Create opportunity", type="primary", disabled=data is None, icon=":material/add_circle:"):
            new = state.run_action(lambda ss: pz.create_opportunity(ss, data), rerun=False)
            if new:
                st.session_state["selected_opportunity_id"] = new.id
                st.session_state["flash"] = f"Created “{new.name}”."
                st.rerun()
    else:
        if b1.button("Save changes", type="primary", disabled=data is None, icon=":material/save:"):
            state.run_action(lambda ss: pz.update_opportunity(ss, o.id, data), "Saved. Ranking updated.")
        confirm = b2.checkbox("Confirm delete", key=f"{k}_delconf")
        if b3.button("Delete", disabled=not confirm, key=f"{k}_del", icon=":material/delete:"):
            st.session_state.pop("selected_opportunity_id", None)
            state.run_action(lambda ss: pz.delete_opportunity(ss, o.id), "Opportunity deleted.")

    if o is not None:
        st.markdown(
            theme.badge("observed") + f" **Supporting evidence** ({len(o.evidence)} records)", unsafe_allow_html=True
        )
        if o.theme_label:
            st.caption(f"Created from theme “{o.theme_label}”.")
        recs = [s.get(Feedback, e.feedback_id) for e in o.evidence[:200]]
        recs = [r for r in recs if r]
        if recs:
            edf = pd.DataFrame(
                [
                    {
                        "ID": r.id,
                        "Feedback": r.text_original,
                        "Source": r.source,
                        "Segment": r.customer_segment,
                        "Sentiment": r.sentiment_label,
                        "Date": r.created_at,
                    }
                    for r in recs
                ]
            )
            st.dataframe(
                edf,
                hide_index=True,
                use_container_width=True,
                height=240,
                column_config={
                    "Feedback": st.column_config.TextColumn(width="large"),
                    "Date": st.column_config.DatetimeColumn(format="YYYY-MM-DD"),
                },
            )
        else:
            st.caption("No evidence linked — this is an unvalidated idea.")
        with st.expander("Edit evidence links"):
            raw = st.text_area(
                "Feedback IDs (one per line or comma-separated)",
                value="\n".join(e.feedback_id for e in o.evidence),
                key=f"{k}_ev",
                height=120,
            )
            if st.button("Save evidence links", key=f"{k}_evsave"):
                ids = [x.strip() for x in raw.replace(",", "\n").splitlines() if x.strip()]
                state.run_action(
                    lambda ss: pz.set_evidence(ss, o.id, ids), "Evidence links updated (unknown IDs ignored)."
                )


def _ranking(opps) -> None:
    rank = pz.ranking_frame(opps)
    f1, f2, f3 = st.columns(3)
    areas = f1.multiselect("Product area", sorted({x for x in rank["Product area"] if x}))
    statuses = f2.multiselect("Status", pz.STATUSES)
    segs = f3.multiselect("Segment", sorted({x for x in rank["Segment"] if x}))
    view = rank
    if areas:
        view = view[view["Product area"].isin(areas)]
    if statuses:
        view = view[view["Status"].isin(statuses)]
    if segs:
        view = view[view["Segment"].isin(segs)]
    st.caption("Click a column header to sort. Rank is computed over all opportunities, before filtering.")
    ev = st.dataframe(
        view.drop(columns=["id"]),
        hide_index=True,
        use_container_width=True,
        on_select="rerun",
        selection_mode="single-row",
        key="rank_table",
        column_config={
            "RICE": st.column_config.NumberColumn(format="%.2f"),
            "Confidence": st.column_config.NumberColumn(format="%.2f"),
            "Reach is proxy": st.column_config.CheckboxColumn("Reach proxy?"),
            "Evidence": st.column_config.NumberColumn("Evidence records"),
        },
    )
    if ev and ev.selection and ev.selection.rows:
        st.session_state["selected_opportunity_id"] = int(view.iloc[ev.selection.rows[0]]["id"])
        st.button("Edit selected opportunity", on_click=lambda: st.session_state.update(prio_view="Edit opportunity"))
    if len(view) >= 3:
        _matrix(view)
    elif not view.empty:
        st.info(
            "Add at least 3 opportunities to see them on a value-vs-effort matrix "
            "(quick wins, big bets, fill-ins and money pits)."
        )


def _matrix_clicked() -> None:
    event = st.session_state.get("value_effort")
    points = event.selection.points if event and event.selection else []
    if points and points[0].get("customdata"):
        st.session_state.update(selected_opportunity_id=int(points[0]["customdata"][0]), prio_view="Edit opportunity")


def _matrix(view: pd.DataFrame) -> None:
    """Value vs effort 2×2 — click a dot to edit that opportunity."""
    st.markdown("#### Value vs effort")
    st.caption("Value = Reach × Impact × Confidence. Lines split at the median. **Click a dot** to edit it.")
    d = view.copy()
    d["Value"] = d["Reach"] * d["Impact"] * d["Confidence"]
    d["Evidence size"] = d["Evidence"].clip(lower=1)
    vx, vy = float(d["Effort"].median()), float(d["Value"].median())

    def quadrant(r) -> str:
        if r["Value"] >= vy:
            return "Quick win" if r["Effort"] <= vx else "Big bet"
        return "Fill-in" if r["Effort"] <= vx else "Money pit"

    d["Quadrant"] = d.apply(quadrant, axis=1)
    fig = px.scatter(
        d,
        x="Effort",
        y="Value",
        size="Evidence size",
        color="Quadrant",
        text="Opportunity",
        custom_data=["id"],
        size_max=40,
        color_discrete_map={"Quick win": "#2A9D8F", "Big bet": "#4F46E5", "Fill-in": "#9AA5B1", "Money pit": "#D1495B"},
        hover_data={"RICE": ":.1f", "Evidence": True, "Evidence size": False, "id": False},
    )
    fig.add_vline(x=vx, line_dash="dot", line_color="#9AA5B1")
    fig.add_hline(y=vy, line_dash="dot", line_color="#9AA5B1")
    fig.update_traces(textposition="top center", textfont_size=11, marker=dict(opacity=0.8, line_width=1))
    fig.update_layout(
        height=420,
        xaxis_title="Effort (person-months) →",
        yaxis_title="Value →",
        legend_title=None,
        legend=dict(orientation="h", y=1.1),
    )
    st.plotly_chart(
        fig, use_container_width=True, on_select=_matrix_clicked, selection_mode="points", key="value_effort"
    )


def _sensitivity(opps) -> None:
    pct = (
        st.slider(
            "Change each input by ±",
            5,
            75,
            25,
            5,
            format="%d%%",
            help="Impact moves one step on its ordinal scale instead.",
        )
        / 100
    )
    sens = pz.sensitivity(opps, pct)
    st.caption(
        "Each cell shows the opportunity's rank after changing that one input (others unchanged). "
        "A wide rank range means the priority depends heavily on an uncertain estimate."
    )
    st.dataframe(sens.drop(columns=["id"]), hide_index=True, use_container_width=True)
    unstable = sens[~sens["Stable"]]
    if not unstable.empty:
        st.info(
            f"{len(unstable)} opportunity(ies) change rank under ±{pct:.0%} — validate their inputs first: "
            + ", ".join(unstable["Opportunity"].head(5))
        )


def _scenarios(s, opps) -> None:
    st.markdown(
        "Edit inputs below to model an alternative scenario. Live opportunities are **not** changed "
        "until you restore a scenario."
    )
    base = pz.current_inputs(opps)
    edit = pd.DataFrame([{"id": o.id, "Opportunity": o.name, **base[str(o.id)]} for o in opps])
    edited = st.data_editor(
        edit,
        hide_index=True,
        use_container_width=True,
        disabled=["id", "Opportunity"],
        key="scenario_editor",
        column_config={
            "reach": st.column_config.NumberColumn("Reach", min_value=0),
            "impact": st.column_config.SelectboxColumn("Impact", options=list(pz.IMPACT_SCALE)),
            "confidence": st.column_config.NumberColumn("Confidence (0–1)", min_value=0.01, max_value=1.0, step=0.05),
            "effort": st.column_config.NumberColumn("Effort (person-months)", min_value=0.1),
        },
    )
    proposed = {str(r["id"]): {f: r[f] for f in pz.RICE_FIELDS} for _, r in edited.iterrows()}
    try:
        cmp = pz.compare_scenarios(opps, base, proposed, "current", "edited")
        st.markdown("**Current vs edited ranking**")
        st.dataframe(
            cmp.drop(columns=["id"]),
            hide_index=True,
            use_container_width=True,
            column_config={"Rank change": st.column_config.NumberColumn(help="Positive = moves up")},
        )
    except Exception as exc:
        st.error(f"Some edited values are invalid: {exc}")
    c1, c2, c3 = st.columns([2, 3, 1])
    sc_name = c1.text_input("Scenario name")
    sc_desc = c2.text_input("Description (optional)")
    if c3.button("Save scenario", disabled=not sc_name):
        state.run_action(lambda ss: pz.save_scenario(ss, sc_name, proposed, sc_desc), f"Scenario “{sc_name}” saved.")

    scenarios = pz.list_scenarios(s)
    st.markdown("**Saved scenarios**")
    if not scenarios:
        st.caption("No saved scenarios yet.")
        return
    labels = {"current": "Current inputs", **{str(sc.id): f"{sc.name} ({sc.created_at:%Y-%m-%d})" for sc in scenarios}}
    inputs = {"current": base, **{str(sc.id): sc.inputs for sc in scenarios}}
    a, b = st.columns(2)
    left = a.selectbox("Compare", list(labels), format_func=labels.get, index=0)
    right = b.selectbox("with", list(labels), format_func=labels.get, index=1)
    st.dataframe(
        pz.compare_scenarios(opps, inputs[left], inputs[right], "left", "right").drop(columns=["id"]),
        hide_index=True,
        use_container_width=True,
    )
    r1, r2, r3 = st.columns([2, 1, 1])
    target = r1.selectbox("Scenario", [sc.id for sc in scenarios], format_func=lambda i: labels[str(i)])
    confirm = r1.checkbox("I understand restoring overwrites the live RICE inputs (the change is logged)")
    if r2.button("Restore", disabled=not confirm):
        state.run_action(lambda ss: pz.apply_scenario(ss, target), "Scenario restored. Scores recalculated.")
    if r3.button("Delete scenario"):
        state.run_action(lambda ss: pz.delete_scenario(ss, target), "Scenario deleted.")


def render() -> None:
    st.session_state["_page_key"] = "prioritization"
    theme.header(
        "Prioritize",
        "Score opportunities with RICE = (Reach × Impact × Confidence) ÷ Effort. "
        "Change an input and watch the rank move.",
        "Decide",
        "leaderboard",
    )
    with state.db().session() as s:
        opps = pz.list_opportunities(s)
        views = ["Ranking", "Edit opportunity", "Sensitivity", "Scenarios", "Score history"]
        if st.session_state.get("prio_view") not in views:
            st.session_state["prio_view"] = views[0]
        view = st.segmented_control("View", views, key="prio_view", label_visibility="collapsed") or views[0]
        st.write("")
        if view == "Ranking":
            if not opps:
                theme.empty_state(
                    "No opportunities yet",
                    "Create one from a theme on AI Insights (evidence is linked automatically) "
                    "or add a manual idea in the Edit opportunity tab.",
                )
                if st.button("Go to AI Insights"):
                    state.go("insights")
            else:
                _ranking(opps)
        elif view == "Edit opportunity":
            _opportunity_editor(s, opps)
        elif view == "Sensitivity":
            if len(opps) < 2:
                st.caption("Add at least two opportunities to analyse ranking sensitivity.")
            else:
                _sensitivity(opps)
        elif view == "Scenarios":
            if not opps:
                st.caption("Add opportunities first.")
            else:
                _scenarios(s, opps)
        elif view == "Score history":
            hist = pz.score_history(s)
            if hist.empty:
                st.caption("No score history yet.")
            else:
                fig = px.line(
                    hist,
                    x="recorded_at",
                    y="score",
                    color="opportunity",
                    markers=True,
                    hover_data=["reason"],
                    title="RICE score over time (each save of changed inputs)",
                )
                fig.update_layout(height=360, xaxis_title=None, legend_title=None)
                st.plotly_chart(fig, use_container_width=True)
                st.dataframe(
                    hist.sort_values("recorded_at", ascending=False).drop(columns=["opportunity_id"]),
                    hide_index=True,
                    use_container_width=True,
                )
        with st.expander("How RICE is calculated here"):
            st.markdown(
                "- **Reach** — customers/accounts affected per quarter. Suggested values from themes are labelled "
                "*proxy*: distinct customer IDs in the evidence (or record count if IDs are missing).\n"
                "- **Impact** — 0.25 minimal · 0.5 low · 1 medium · 2 high · 3 massive.\n"
                "- **Confidence** — 10–100%, stored as a fraction (anchors 50/80/100%).\n"
                "- **Effort** — person-months; must be greater than zero.\n"
                "- Scores are deterministic: the same inputs always give the same score and rank (ties share a rank)."
            )
