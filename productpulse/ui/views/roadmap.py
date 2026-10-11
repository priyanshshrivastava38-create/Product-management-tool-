"""Product Roadmap — place opportunities on horizons, version the plan, export briefs."""

from __future__ import annotations

import streamlit as st
from streamlit_sortables import sort_items

from productpulse.exports import briefs
from productpulse.services import prioritization as pz
from productpulse.services import roadmap as rm
from productpulse.ui import state, theme

BOARD_STYLE = """
.sortable-component { background: transparent; border: none; padding: 0; gap: 12px; display: flex;
                      flex-direction: row; align-items: stretch; }
.sortable-container { background: rgba(148,163,184,0.08); border: 1px solid rgba(100,116,139,0.22);
                      border-radius: 14px; min-width: 200px; flex: 1 1 0; padding: 6px; }
.sortable-container-header { background: transparent; font-weight: 700; font-size: 1rem; padding: 8px 10px; }
.sortable-container-body { background: transparent; min-height: 260px; padding: 4px;
                           display: flex; flex-direction: column; }
.sortable-item { background: #ffffff; color: #1F2937; border: 1px solid rgba(100,116,139,0.25);
                 border-left: 4px solid #4F46E5; border-radius: 10px; padding: 10px 12px; margin: 6px 4px;
                 font-size: 0.9rem; box-shadow: 0 1px 2px rgba(15,23,42,0.06); cursor: grab; }
.sortable-item:hover { box-shadow: 0 4px 12px rgba(79,70,229,0.18); transform: translateY(-1px); }
"""


def _card_text(it) -> str:
    o = it.opportunity
    owner = f" · {it.owner}" if it.owner else ""
    return f"{o.name}  —  RICE {pz.score_of(o):,.0f} · {it.status}{owner}"


def _board(s) -> None:
    items = rm.items(s)
    hz = rm.horizons(s)
    label_to_id: dict[str, int] = {}
    layout = []
    for h in hz:
        labels = []
        for it in (i for i in items if i.horizon == h):
            text, n = _card_text(it), 2
            while text in label_to_id:  # keep labels unique so drags map back to the right item
                text, n = f"{_card_text(it)} ({n})", n + 1
            label_to_id[text] = it.id
            labels.append(text)
        layout.append({"header": h, "items": labels})
    st.caption(
        ":material/drag_indicator: Drag cards between columns, or up and down, to reprioritise — "
        "changes save automatically."
    )
    signature = "|".join(f"{c['header']}:{','.join(c['items'])}" for c in layout)
    result = sort_items(
        layout,
        multi_containers=True,
        direction="vertical",
        custom_style=BOARD_STYLE,
        key=f"board_{abs(hash(signature)) % 10**8}",
    )
    new_layout = {c["header"]: [label_to_id[x] for x in c["items"]] for c in result}
    old_layout = {c["header"]: [label_to_id[x] for x in c["items"]] for c in layout}
    if new_layout != old_layout:
        state.run_action(lambda ss: rm.apply_layout(ss, new_layout), "Roadmap updated.")

    st.write("")
    with st.expander("Details, reorder with buttons, and edit", expanded=False):
        for h in hz:
            its = [i for i in items if i.horizon == h]
            if not its:
                continue
            st.markdown(f"**{h}**")
            for idx, it in enumerate(its):
                o = it.opportunity
                c1, c2, c3, c4 = st.columns([6, 0.6, 0.6, 1])
                bits = [f"RICE {pz.score_of(o):,.1f}", it.status]
                if it.target_date:
                    bits.append(f"target {it.target_date:%b %d, %Y}")
                if it.dependencies:
                    bits.append(f"depends on {it.dependencies}")
                c1.markdown(
                    f"{idx + 1}. **{o.name}** — "
                    + " · ".join(bits)
                    + (" · reach is an estimate" if o.reach_is_proxy else "")
                )
                if c2.button("↑", key=f"up_{it.id}", disabled=idx == 0, help="Move up"):
                    state.run_action(lambda ss, i=it.id: rm.move(ss, i, -1))
                if c3.button("↓", key=f"dn_{it.id}", disabled=idx == len(its) - 1, help="Move down"):
                    state.run_action(lambda ss, i=it.id: rm.move(ss, i, 1))
                c4.button(
                    "Edit",
                    key=f"ed_{it.id}",
                    on_click=lambda i=it.id: st.session_state.update(
                        roadmap_edit=i, roadmap_view=":material/edit: Edit initiative"
                    ),
                )


def _editor(s) -> None:
    items = rm.items(s)
    if not items:
        return
    ids = [i.id for i in items]
    cur = st.session_state.get("roadmap_edit", ids[0])
    if cur not in ids:
        cur = ids[0]
    item_id = st.selectbox(
        "Initiative",
        ids,
        index=ids.index(cur),
        format_func=lambda i: next(x.opportunity.name for x in items if x.id == i),
    )
    it = next(x for x in items if x.id == item_id)
    k = f"ri{it.id}"
    hz = rm.horizons(s)
    c1, c2, c3 = st.columns(3)
    horizon = c1.selectbox("Horizon", [*hz, "Custom…"], index=hz.index(it.horizon), key=f"{k}_h")
    if horizon == "Custom…":
        horizon = c1.text_input("Custom horizon name", placeholder="e.g. Q1 2027", key=f"{k}_hc")
    status = c2.selectbox("Status", rm.ITEM_STATUSES, index=rm.ITEM_STATUSES.index(it.status), key=f"{k}_s")
    owner = c3.text_input("Owner", value=it.owner, key=f"{k}_o")
    c4, c5 = st.columns([1, 2])
    has_date = c4.checkbox("Has target date", value=it.target_date is not None, key=f"{k}_hd")
    target = c4.date_input(
        "Target date", value=it.target_date.date() if it.target_date else None, disabled=not has_date, key=f"{k}_td"
    )
    deps = c5.text_input(
        "Dependencies", value=it.dependencies, key=f"{k}_dep", placeholder="e.g. SSO reliability work; data export API"
    )
    rationale = st.text_area("Rationale", value=it.rationale, key=f"{k}_rat", height=80)
    b1, b2, _ = st.columns([1, 1, 3])
    if b1.button("Save", type="primary", key=f"{k}_save"):
        state.run_action(
            lambda ss: rm.update_item(
                ss,
                it.id,
                horizon=horizon or it.horizon,
                owner=owner,
                status=status,
                target_date=target if has_date else None,
                clear_target_date=not has_date,
                dependencies=deps,
                rationale=rationale,
            ),
            "Roadmap updated.",
        )
    if b2.button("Remove from roadmap", key=f"{k}_rm"):
        state.run_action(lambda ss: rm.remove(ss, it.id), "Removed from roadmap.")


def render() -> None:
    st.session_state["_page_key"] = "roadmap"
    theme.header(
        "Roadmap", "Drag initiatives between Now, Next and Later, keep versions, and export briefs.", "Decide", "map"
    )
    with state.db().session() as s:
        opps = pz.list_opportunities(s)
        if not opps:
            theme.empty_state("No opportunities to plan", "Create and prioritise opportunities first.")
            if st.button("Go to Feature Prioritization"):
                state.go("prioritization")
            return
        on_map = {i.opportunity_id for i in rm.items(s)}
        rank = pz.ranking_frame(opps)
        available = [int(r["id"]) for _, r in rank.iterrows() if int(r["id"]) not in on_map]
        names = {int(r["id"]): f"#{r['Rank']} {r['Opportunity']} (RICE {r['RICE']:,.1f})" for _, r in rank.iterrows()}

        with st.container(border=True):
            st.markdown("**Add opportunities** (ordered by RICE rank)")
            c1, c2, c3 = st.columns([3, 1, 1])
            pick = c1.multiselect(
                "Opportunities",
                available,
                format_func=names.get,
                label_visibility="collapsed",
                placeholder="Choose opportunities…" if available else "All opportunities are on the roadmap",
            )
            horizon = c2.selectbox("Horizon", rm.horizons(s), index=1, label_visibility="collapsed")
            if c3.button("Add to roadmap", type="primary", disabled=not pick):
                state.run_action(lambda ss: rm.add_to_roadmap(ss, pick, horizon), f"Added {len(pick)} to {horizon}.")

        views = [
            ":material/view_kanban: Board",
            ":material/edit: Edit initiative",
            ":material/history: Versions",
            ":material/ios_share: Export & brief",
        ]
        if st.session_state.get("roadmap_view") not in views:
            st.session_state["roadmap_view"] = views[0]
        view = st.segmented_control("View", views, key="roadmap_view", label_visibility="collapsed") or views[0]
        if view == views[0]:
            if not on_map:
                st.caption("The roadmap is empty. Add opportunities above.")
            else:
                _board(s)
        elif view == views[1]:
            if on_map:
                _editor(s)
            else:
                st.caption("Nothing to edit yet.")
        elif view == views[2]:
            c1, c2, c3 = st.columns([2, 3, 1])
            name = c1.text_input("Version name", placeholder="e.g. Q4 planning v1")
            note = c2.text_input("Note")
            if c3.button("Save version", disabled=not name):
                state.run_action(lambda ss: rm.save_snapshot(ss, name, note), f"Saved version “{name}”.")
            snaps = rm.list_snapshots(s)
            if not snaps:
                st.caption("No saved versions. Save one to compare later changes.")
            else:
                opts = {
                    "current": "Current roadmap",
                    **{str(x.id): f"{x.name} ({x.created_at:%Y-%m-%d %H:%M})" for x in snaps},
                }
                data = {"current": rm.current_as_snapshot_items(s), **{str(x.id): x.items for x in snaps}}
                a, b = st.columns(2)
                old = a.selectbox("From", list(opts), index=1, format_func=opts.get)
                new = b.selectbox("To", list(opts), index=0, format_func=opts.get)
                diff = rm.compare(data[old], data[new])
                if diff.empty:
                    st.success("No differences.")
                else:
                    st.dataframe(diff, hide_index=True, use_container_width=True)
        elif view == views[3]:
            board = rm.board_frame(s)
            if board.empty:
                st.caption("Add initiatives to export the roadmap.")
            else:
                c1, c2 = st.columns(2)
                c1.download_button(
                    "Download roadmap (CSV)",
                    briefs.to_csv_bytes(board.drop(columns=["item_id"])),
                    "roadmap.csv",
                    "text/csv",
                )
                c2.download_button(
                    "Download roadmap (Markdown)",
                    briefs.roadmap_markdown(s, rm.items(s)).encode(),
                    "roadmap.md",
                    "text/markdown",
                )
            st.markdown("**Product brief**")
            candidates = [i.opportunity for i in rm.items(s)] or opps
            sel = st.multiselect(
                "Initiatives to include",
                [o.id for o in candidates],
                default=[candidates[0].id],
                format_func=lambda i: next(o.name for o in candidates if o.id == i),
            )
            if sel:
                md = briefs.briefs_for(s, [o for o in candidates if o.id in sel])
                st.download_button(
                    "Download product brief (Markdown)",
                    md.encode(),
                    "product_brief.md",
                    "text/markdown",
                    type="primary",
                )
                with st.container(border=True, height=520):
                    st.markdown(md)
