from __future__ import annotations

import pytest
from conftest import MESSY_SAMPLE, SMALL_SAMPLE, csv_bytes
from sqlalchemy import func, select

from productpulse.db.models import Feedback, ImportBatch
from productpulse.ingestion import csv_import as ci


def test_suggest_mapping_uses_synonyms():
    headers = ["Ticket #", "Comment", "Submitted", "Channel", "Account", "Plan", "Area", "Stars", "ARR"]
    m = ci.suggest_mapping(headers)
    assert m["feedback_text"] == "Comment"
    assert m["created_at"] == "Submitted"
    assert m["source"] == "Channel"
    assert m["customer_id"] == "Account"
    assert m["customer_segment"] == "Plan"
    assert m["product_area"] == "Area"
    assert m["rating"] == "Stars"
    assert m["customer_value"] == "ARR"
    assert m["feedback_id"] is None  # "Ticket #" is not a known synonym → user maps it manually


def test_mapping_validation_requires_text_and_unique_columns():
    errors = ci.validate_mapping({"feedback_text": None}, ["a"])
    assert any("Feedback text" in e for e in errors)
    errors = ci.validate_mapping({"feedback_text": "a", "source": "a"}, ["a"])
    assert any("mapped once" in e for e in errors)
    errors = ci.validate_mapping({"feedback_text": "zzz"}, ["a"])
    assert any("not in the file" in e for e in errors)


def test_messy_file_reports_every_problem(settings):
    parsed = ci.read_csv_bytes(MESSY_SAMPLE.read_bytes(), MESSY_SAMPLE.name, settings)
    mapping = ci.suggest_mapping(parsed.headers) | {"feedback_id": "Ticket #"}
    res = ci.validate_rows(parsed, mapping, settings)
    reasons = " | ".join(res.rejected["reason"])
    assert "Feedback text is empty" in reasons
    assert "Unrecognised date 'not a date'" in reasons
    assert "in the future" in reasons
    assert "Malformed row: expected 9 fields, found 10" in reasons
    assert "Duplicate record: same feedback ID as row 3" in reasons
    assert "no letters" in reasons  # "12345"
    assert res.n_rejected == 6
    assert res.n_valid == 6
    assert any("rating" in w for w in res.warnings)  # "five"
    # Currency/thousand separators parsed; formula-looking text kept verbatim, never evaluated
    row = res.valid.set_index("external_id")
    assert row.loc["T-1", "customer_value"] == 120000
    assert row.loc["T-8", "text_original"].startswith("=HYPERLINK(")
    # Whitespace normalised for analysis, original preserved
    assert row.loc["T-10", "text_normalized"] == "Search does not find documents by title"
    assert row.loc["T-10", "text_original"].startswith("   Search   does")
    # Mixed date formats parse
    assert str(row.loc["T-3", "created_at"].date()) == "2026-09-05"
    assert str(row.loc["T-10", "created_at"]) == "2026-09-11 10:30:00"


@pytest.mark.parametrize(
    ("data", "name", "message"),
    [
        (b"", "x.csv", "empty"),
        (b"   \n  ", "x.csv", "empty"),
        (b"feedback_text\n", "x.csv", "no data rows"),
        (b"PK\x03\x04\x00\x00binary", "x.csv", "binary"),
        (b"feedback_text\nhello there", "x.xlsx", ".csv"),
    ],
)
def test_file_level_rejections(settings, data, name, message):
    with pytest.raises(ci.ImportValidationError, match=message):
        ci.read_csv_bytes(data, name, settings)


def test_size_and_row_limits(settings):
    small = settings.model_copy(update={"max_upload_mb": 0.0001, "max_import_rows": 2})
    with pytest.raises(ci.ImportValidationError, match="limit"):
        ci.read_csv_bytes(b"feedback_text\n" + b"word " * 100, "x.csv", small)
    limited = settings.model_copy(update={"max_import_rows": 2})
    with pytest.raises(ci.ImportValidationError, match="more than 2"):
        ci.read_csv_bytes(csv_bytes("feedback_text", "a b c", "d e f", "g h i"), "x.csv", limited)


def test_encoding_and_delimiter_detection(settings):
    data = "feedback_text;source\nCafé crashes on login;Support\nRésumé upload is slow;Survey\n".encode("cp1252")
    parsed = ci.read_csv_bytes(data, "x.csv", settings)
    assert parsed.encoding == "cp1252"
    assert parsed.delimiter == ";"
    assert parsed.rows[0][0] == "Café crashes on login"


def test_day_first_dates(settings):
    data = csv_bytes("feedback_text,created_at", "Export is broken,03/04/2026")
    parsed = ci.read_csv_bytes(data, "x.csv", settings)
    m = ci.suggest_mapping(parsed.headers)
    assert ci.validate_rows(parsed, m, settings, "day_first").valid.iloc[0]["created_at"].month == 4
    assert ci.validate_rows(parsed, m, settings, "auto").valid.iloc[0]["created_at"].month == 3


def test_missing_metadata_is_allowed(settings, db):
    data = csv_bytes("text", "Dashboard is slow to load", "Please add dark mode")
    with db.session() as s:
        summary, result = ci.import_csv(s, data, "minimal.csv", settings)
    assert summary.inserted == 2
    assert any("No date column" in w for w in result.warnings)
    with db.session() as s:
        fb = s.scalars(select(Feedback)).first()
        assert fb.source is None and fb.created_at is None and fb.customer_segment is None


def test_repeat_import_does_not_duplicate(db, settings):
    data = SMALL_SAMPLE.read_bytes()
    with db.session() as s:
        first, _ = ci.import_csv(s, data, SMALL_SAMPLE.name, settings)
    with db.session() as s:
        second, _ = ci.import_csv(s, data, SMALL_SAMPLE.name, settings)
    assert first.inserted > 100
    assert second.inserted == 0
    assert second.already_imported == first.inserted
    with db.session() as s:
        assert s.scalar(select(func.count()).select_from(Feedback)) == first.inserted
        assert s.scalar(select(func.count()).select_from(ImportBatch)) == 2  # history retained


def test_stable_ids_without_source_ids(db, settings):
    data = csv_bytes(
        "feedback_text,created_at,customer_id", "Search is broken,2026-01-02,C1", "Search is broken,2026-01-03,C2"
    )
    with db.session() as s:
        a, _ = ci.import_csv(s, data, "a.csv", settings)
    with db.session() as s:
        b, _ = ci.import_csv(s, data, "a.csv", settings)
    assert a.inserted == 2 and b.inserted == 0
    # Same text from different customers: both kept, second flagged as an exact-text duplicate
    assert a.flagged_text_duplicates == 1
    with db.session() as s:
        rows = list(s.scalars(select(Feedback).order_by(Feedback.created_at)))
        assert rows[1].duplicate_of_id == rows[0].id
    assert ci.stable_feedback_id(None, None, "x", None, None) == ci.stable_feedback_id(None, None, "x", None, None)


def test_in_file_exact_duplicates_rejected_with_reason(settings):
    data = csv_bytes("feedback_text,created_at", "Same words here,2026-01-01", "Same words here,2026-01-01")
    parsed = ci.read_csv_bytes(data, "x.csv", settings)
    res = ci.validate_rows(parsed, ci.suggest_mapping(parsed.headers), settings)
    assert res.n_valid == 1 and res.n_rejected == 1
    assert "Duplicate record" in res.rejected.iloc[0]["reason"]


def test_text_length_limit(settings):
    tiny = settings.model_copy(update={"max_text_chars": 20})
    data = csv_bytes("feedback_text", "short text ok", "this feedback is definitely longer than twenty characters")
    parsed = ci.read_csv_bytes(data, "x.csv", tiny)
    res = ci.validate_rows(parsed, ci.suggest_mapping(parsed.headers), tiny)
    assert res.n_valid == 1
    assert "exceeds" in res.rejected.iloc[0]["reason"]
