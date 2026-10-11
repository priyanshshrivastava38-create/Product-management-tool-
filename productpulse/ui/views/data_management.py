"""Data Management — CSV import with column mapping and validation, history, exports, reset."""

from __future__ import annotations

import pandas as pd
import streamlit as st
from sqlalchemy import select

from productpulse.config import SAMPLE_DIR
from productpulse.db.models import Feedback
from productpulse.exports.briefs import to_csv_bytes
from productpulse.ingestion import csv_import as ci
from productpulse.services import feedback as fb
from productpulse.services import habits, workspace
from productpulse.ui import state, theme
from productpulse.ui.views.common import load_sample_button

DATE_ORDERS = {
    "auto": "Automatic (ISO / month-first)",
    "day_first": "Day first (31/12/2026)",
    "month_first": "Month first (12/31/2026)",
}


def _upload() -> None:
    settings = state.settings()
    up = st.file_uploader(
        "Upload feedback CSV",
        type=["csv"],
        help=f"UTF-8 or Windows-1252, up to {settings.max_upload_mb:g} MB and " f"{settings.max_import_rows:,} rows.",
    )
    if up is None:
        return
    data = up.getvalue()
    try:
        parsed = ci.read_csv_bytes(data, up.name, settings)
    except ci.ImportValidationError as exc:
        st.error(f"Cannot import **{up.name}**: {exc}")
        return
    st.success(
        f"Read {len(parsed.rows) + len(parsed.malformed):,} data rows · encoding {parsed.encoding} · "
        f"delimiter “{parsed.delimiter}”."
    )
    st.markdown("**Preview** (first 20 rows, shown as plain text — nothing in the file is executed)")
    st.dataframe(parsed.preview, hide_index=True, use_container_width=True)

    st.markdown("**Map columns** to the canonical schema")
    with state.db().session() as s_:
        remembered = habits.remembered_mapping(s_, parsed.headers)
    suggested = remembered or ci.suggest_mapping(parsed.headers)
    if remembered:
        st.success(
            "We recognise this file format — using the same column mapping as last time.", icon=":material/history:"
        )
    options = ["— not mapped —", *parsed.headers]
    mapping: dict[str, str | None] = {}
    cols = st.columns(5)
    for i, spec in enumerate(ci.CANONICAL_FIELDS):
        default = suggested.get(spec.name)
        choice = cols[i % 5].selectbox(
            spec.label + (" *" if spec.required else ""),
            options,
            index=options.index(default) if default else 0,
            key=f"map_{parsed.sha256[:8]}_{spec.name}",
            help=spec.description,
        )
        mapping[spec.name] = None if choice == options[0] else choice
    date_order = st.radio(
        "Date format", list(DATE_ORDERS), format_func=DATE_ORDERS.get, horizontal=True, key=f"dates_{parsed.sha256[:8]}"
    )
    errors = ci.validate_mapping(mapping, parsed.headers)
    for e in errors:
        st.warning(e)
    if errors:
        return

    try:
        result = ci.validate_rows(parsed, mapping, settings, date_order)
    except ci.ImportValidationError as exc:
        st.error(str(exc))
        return
    theme.cards(
        [
            ("Valid rows", f"{result.n_valid:,}", "ready to import", "check_circle", "green"),
            (
                "Rejected rows",
                f"{result.n_rejected:,}",
                "see reasons below",
                "block",
                "red" if result.n_rejected else "",
            ),
            (
                "Warnings",
                str(len(result.warnings)),
                "imported with blanks",
                "warning",
                "amber" if result.warnings else "",
            ),
            ("Missing dates", f"{result.missing_dates:,}", "excluded from trends", "event_busy"),
        ]
    )
    for w in result.warnings:
        st.caption(f":material/warning: {w}")
    if result.n_rejected:
        with st.expander(f"Rejected rows ({result.n_rejected})", expanded=result.n_valid == 0):
            st.dataframe(result.rejected, hide_index=True, use_container_width=True)
            st.download_button(
                "Download rejected rows with reasons (CSV)",
                to_csv_bytes(result.rejected),
                f"rejected_{up.name}",
                "text/csv",
            )
    if not result.n_valid:
        return

    def do_import(s):
        habits.remember_mapping(s, parsed.headers, mapping)
        return ci.import_validated(s, result)

    b1, b2, _ = st.columns([1.4, 1, 2])
    if b1.button(
        f"Import {result.n_valid:,} rows & analyse",
        type="primary",
        icon=":material/auto_awesome:",
        use_container_width=True,
        help="Imports the valid rows, then refreshes themes and trends.",
    ):
        summary = state.run_action(do_import, rerun=False)
        if summary and state.analyze():
            st.session_state["flash"] = (
                f"Imported {summary.inserted:,} new records "
                f"({summary.already_imported:,} already present) and refreshed the analysis."
            )
            state.go("dashboard")
    if b2.button("Import only", icon=":material/upload:", use_container_width=True):
        summary = state.run_action(do_import, rerun=False)
        if summary:
            st.session_state["last_import"] = summary
            st.rerun()


def render() -> None:
    st.session_state["_page_key"] = "data"
    theme.header(
        "Import & data",
        "Bring in feedback from any tool, review past imports, export or reset your workspace.",
        "Workspace",
        "upload_file",
    )
    # Keep the summary until the user acts on it, so the button below survives reruns.
    last = st.session_state.get("last_import")
    if last:
        st.success(
            f"Import #{last.batch_id}: {last.inserted:,} new record(s), {last.already_imported:,} already "
            f"present (skipped), {last.rejected:,} rejected, {last.flagged_text_duplicates:,} flagged as "
            "exact-text duplicates (kept)."
        )
        c1, c2, _ = st.columns([1.4, 0.6, 3])
        if c2.button("Dismiss"):
            st.session_state.pop("last_import", None)
            st.rerun()
        if c1.button("Run analysis on the updated workspace", type="primary"):
            run = state.analyze()
            if run:
                st.session_state.pop("last_import", None)
                st.session_state["flash"] = f"Analysis complete: {run.n_themes} themes."
                state.go("insights")

    tabs = st.tabs(["Import CSV", "Sample data", "Import history", "Export & reset"])
    with tabs[0]:
        with st.expander("Canonical schema"):
            st.dataframe(
                pd.DataFrame(
                    [
                        {
                            "Field": f.name,
                            "Required": "yes" if f.required else "",
                            "Accepted header names": ", ".join(f.synonyms[:6]),
                            "Notes": f.description,
                        }
                        for f in ci.CANONICAL_FIELDS
                    ]
                ),
                hide_index=True,
                use_container_width=True,
            )
            st.caption(
                "Re-importing the same records is safe: IDs are derived from the source ID (or the record's "
                "content), so existing records are skipped rather than duplicated."
            )
        _upload()
    with tabs[1]:
        st.markdown("All bundled data is **synthetic** (fictional product, customers and quotes).")
        c1, c2, c3 = st.columns(3)
        for col, fname, label in (
            (c1, "sample_feedback.csv", "Small sample (≈140 rows)"),
            (c2, "synthetic_feedback.csv", "Full synthetic dataset (≈1,400 rows)"),
            (c3, "messy_feedback_example.csv", "Messy example (column mapping + errors)"),
        ):
            path = SAMPLE_DIR / fname
            if path.exists():
                col.download_button(label, path.read_bytes(), fname, "text/csv", key=f"dl_{fname}")
        st.write("")
        load_sample_button("data_sample", "Load full sample workspace and analyse")
    with state.db().session() as s:
        with tabs[2]:
            batches = workspace.import_batches(s)
            if not batches:
                st.caption("No imports yet.")
            else:
                st.dataframe(
                    pd.DataFrame(
                        [
                            {
                                "Import": b.id,
                                "When": b.created_at,
                                "File": b.filename,
                                "Rows": b.total_rows,
                                "Valid": b.valid_rows,
                                "New": b.inserted_rows,
                                "Already present": b.already_imported_rows,
                                "Rejected": b.rejected_rows,
                                "Encoding": b.encoding,
                                "Sample": b.is_sample,
                                "Mapping": ", ".join(f"{k}←{v}" for k, v in b.column_mapping.items()),
                            }
                            for b in batches
                        ]
                    ),
                    hide_index=True,
                    use_container_width=True,
                )
                c1, c2 = st.columns([2, 1])
                bid = c1.selectbox(
                    "Delete an import and all its records",
                    [b.id for b in batches],
                    format_func=lambda i: next(f"#{b.id} {b.filename}" for b in batches if b.id == i),
                )
                confirm = c1.checkbox("I understand this permanently deletes these feedback records")
                if c2.button("Delete import", disabled=not confirm):
                    state.run_action(
                        lambda ss: workspace.delete_batch(ss, bid),
                        "Import deleted. Re-run the analysis to refresh themes.",
                    )
        with tabs[3]:
            total = fb.count(s)
            st.markdown(f"**Export** — {total:,} feedback records")
            if total:
                st.download_button(
                    "Download all feedback with analysis (CSV)",
                    to_csv_bytes(_export_frame(s)),
                    "productpulse_feedback.csv",
                    "text/csv",
                )
                st.caption(
                    "Includes sentiment and theme columns. Formula-like cells are prefixed with ' to prevent "
                    "spreadsheet formula execution."
                )
            st.divider()
            st.markdown("**Danger zone — reset workspace**")
            st.caption(
                "Deletes all feedback, imports, analysis runs, themes, opportunities, scenarios, roadmap items, "
                "versions and audit history from the configured database."
            )
            typed = st.text_input("Type RESET to confirm")
            if st.button("Reset workspace", disabled=typed != "RESET"):
                for key in ("selected_theme_id", "selected_feedback_id", "selected_opportunity_id", "roadmap_edit"):
                    st.session_state.pop(key, None)
                state.run_action(workspace.reset, "Workspace reset.")


def _export_frame(s) -> pd.DataFrame:
    rows = list(s.scalars(select(Feedback).order_by(Feedback.created_at.nulls_last(), Feedback.id)))
    tmap = fb.active_theme_map(s, [r.id for r in rows])
    return pd.DataFrame(
        [
            {
                "id": r.id,
                "source_id": r.external_id,
                "feedback_text": r.text_original,
                "created_at": r.created_at,
                "source": r.source,
                "customer_id": r.customer_id,
                "customer_segment": r.customer_segment,
                "product_area": r.product_area,
                "rating": r.rating,
                "customer_value": r.customer_value,
                "status": r.status,
                "sentiment": r.sentiment_label,
                "sentiment_score": r.sentiment_score,
                "sentiment_reliability": r.sentiment_reliability,
                "theme": tmap.get(r.id, (None, None))[1],
                "duplicate_of": r.duplicate_of_id,
            }
            for r in rows
        ]
    )
