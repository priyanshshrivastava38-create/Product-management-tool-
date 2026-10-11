"""CSV ingestion: parse → map columns → validate → import.

Design rules
- Uploaded content is treated strictly as data: cells are never evaluated and
  formula-looking values are stored verbatim (and neutralised on CSV export).
- Original feedback text is preserved byte-for-byte (after decoding) in
  ``text_original``; a whitespace-normalised copy is used for analysis.
- Records get a stable ID derived from the source ID (or, if absent, from the
  record's content), so re-importing the same file does not duplicate rows.
- Nothing is dropped silently: every rejected row has a reason and can be
  downloaded; exact-text duplicates are imported but flagged.
"""

from __future__ import annotations

import csv
import hashlib
import io
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Literal

import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from productpulse.config import Settings
from productpulse.db.models import Feedback, ImportBatch
from productpulse.services import audit


class ImportValidationError(ValueError):
    """A file-level problem that prevents import (shown to the user verbatim)."""


@dataclass(frozen=True)
class FieldSpec:
    name: str
    label: str
    required: bool
    synonyms: tuple[str, ...]
    description: str


CANONICAL_FIELDS: tuple[FieldSpec, ...] = (
    FieldSpec(
        "feedback_text",
        "Feedback text",
        True,
        (
            "feedback_text",
            "feedback",
            "text",
            "comment",
            "comments",
            "message",
            "body",
            "review",
            "review_text",
            "description",
            "content",
            "verbatim",
            "response",
            "note",
        ),
        "The customer's words. Required.",
    ),
    FieldSpec(
        "feedback_id",
        "Feedback ID",
        False,
        ("feedback_id", "id", "ticket_id", "review_id", "response_id", "record_id", "uid"),
        "Source system identifier. Used for de-duplication on re-import.",
    ),
    FieldSpec(
        "created_at",
        "Created at",
        False,
        (
            "created_at",
            "date",
            "created",
            "timestamp",
            "submitted_at",
            "created_date",
            "time",
            "datetime",
            "submitted",
            "review_date",
        ),
        "When the feedback was given. Needed for trends.",
    ),
    FieldSpec(
        "source",
        "Source",
        False,
        ("source", "channel", "origin", "platform", "feedback_source"),
        "Support ticket, app review, survey, interview, CSM note, ...",
    ),
    FieldSpec(
        "customer_id",
        "Customer ID",
        False,
        ("customer_id", "customer", "account_id", "user_id", "client_id", "account", "company"),
        "Used to count unique customers (a better reach proxy than record counts).",
    ),
    FieldSpec(
        "customer_segment",
        "Customer segment",
        False,
        ("customer_segment", "segment", "plan", "tier", "customer_tier", "persona"),
        "e.g. Enterprise, Mid-Market, SMB.",
    ),
    FieldSpec(
        "product_area",
        "Product area",
        False,
        ("product_area", "area", "feature", "component", "module", "category", "product"),
        "Part of the product the feedback concerns.",
    ),
    FieldSpec("rating", "Rating", False, ("rating", "score", "stars", "nps", "csat"), "Numeric rating (0–10)."),
    FieldSpec(
        "customer_value",
        "Customer value",
        False,
        ("customer_value", "revenue", "arr", "mrr", "account_value", "contract_value", "acv"),
        "Revenue or account value. Currency symbols and thousands separators are stripped.",
    ),
    FieldSpec(
        "status",
        "Status",
        False,
        ("status", "state", "ticket_status", "resolution"),
        "Workflow status in the source system.",
    ),
)
FIELD_NAMES = tuple(f.name for f in CANONICAL_FIELDS)
MAX_SHORT_FIELD = 100
MAX_ID_FIELD = 255


def _norm_header(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", value.strip().lower()).strip("_")


def normalize_text(text: str) -> str:
    """NFKC-normalise, drop control characters, collapse whitespace."""
    text = unicodedata.normalize("NFKC", text)
    text = "".join(ch if (ch.isprintable() or ch in "\n\t ") else " " for ch in text)
    return re.sub(r"\s+", " ", text).strip()


def text_hash(normalized: str) -> str:
    return hashlib.sha256(normalized.lower().encode("utf-8")).hexdigest()


def stable_feedback_id(
    external_id: str | None,
    source: str | None,
    normalized: str,
    created_at: datetime | None,
    customer_id: str | None,
) -> str:
    if external_id:
        key = f"ext|{(source or '').lower()}|{external_id}"
    else:
        key = "|".join(
            [
                "content",
                normalized.lower(),
                created_at.isoformat() if created_at else "",
                (customer_id or "").lower(),
                (source or "").lower(),
            ]
        )
    return "fb_" + hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]


# --------------------------------------------------------------------------- parse


@dataclass
class ParsedFile:
    filename: str
    sha256: str
    encoding: str
    delimiter: str
    headers: list[str]
    rows: list[list[str]]  # well-formed data rows
    row_numbers: list[int]  # 1-based line number in the file for each row (header = 1)
    malformed: list[dict] = field(default_factory=list)

    @property
    def preview(self) -> pd.DataFrame:
        return pd.DataFrame(self.rows[:20], columns=self.headers)


def read_csv_bytes(data: bytes, filename: str, settings: Settings) -> ParsedFile:
    if not filename.lower().endswith(".csv"):
        raise ImportValidationError("Only .csv files are supported.")
    size_mb = len(data) / (1024 * 1024)
    if size_mb > settings.max_upload_mb:
        raise ImportValidationError(
            f"File is {size_mb:.1f} MB; the limit is {settings.max_upload_mb:g} MB " "(PRODUCTPULSE_MAX_UPLOAD_MB)."
        )
    if not data.strip():
        raise ImportValidationError("The file is empty.")
    if b"\x00" in data[:8192]:
        raise ImportValidationError("The file looks binary (e.g. an Excel workbook). Export it as CSV first.")

    text, encoding = None, None
    for enc in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            text, encoding = data.decode(enc), enc
            break
        except UnicodeDecodeError:
            continue
    assert text is not None and encoding is not None  # latin-1 always decodes

    sample = text[:16384]
    try:
        delimiter = csv.Sniffer().sniff(sample, delimiters=",;\t|").delimiter
    except csv.Error:
        delimiter = ","

    csv.field_size_limit(max(settings.max_text_chars * 4, 131072))
    reader = csv.reader(io.StringIO(text, newline=""), delimiter=delimiter)
    try:
        headers = next(reader)
    except StopIteration as exc:
        raise ImportValidationError("The file has no header row.") from exc
    except csv.Error as exc:
        raise ImportValidationError(f"Could not read the header row: {exc}") from exc

    headers = [h.strip() for h in headers]
    if not any(headers):
        raise ImportValidationError("The header row is empty.")
    seen: dict[str, int] = {}
    for i, h in enumerate(headers):  # make blank/duplicate header names unique
        base = h or f"column_{i + 1}"
        seen[base] = seen.get(base, 0) + 1
        headers[i] = base if seen[base] == 1 else f"{base}_{seen[base]}"

    rows, numbers, malformed = [], [], []
    try:
        for row in reader:
            line = reader.line_num
            if not row or all(not c.strip() for c in row):
                continue  # blank line
            if len(rows) + len(malformed) >= settings.max_import_rows:
                raise ImportValidationError(
                    f"The file has more than {settings.max_import_rows:,} data rows "
                    "(PRODUCTPULSE_MAX_IMPORT_ROWS). Split it into smaller files."
                )
            if len(row) != len(headers):
                malformed.append(
                    {
                        "row_number": line,
                        "reason": f"Malformed row: expected {len(headers)} fields, found {len(row)}.",
                        "raw": delimiter.join(row)[:500],
                    }
                )
                continue
            rows.append(row)
            numbers.append(line)
    except csv.Error as exc:
        raise ImportValidationError(f"CSV parsing failed near line {reader.line_num}: {exc}") from exc

    if not rows and not malformed:
        raise ImportValidationError("The file has a header row but no data rows.")
    return ParsedFile(
        filename, hashlib.sha256(data).hexdigest(), encoding, delimiter, headers, rows, numbers, malformed
    )


def suggest_mapping(headers: list[str]) -> dict[str, str | None]:
    """Map canonical fields to file columns by exact name, then by synonym."""
    normalized = {_norm_header(h): h for h in headers}
    used: set[str] = set()
    mapping: dict[str, str | None] = {}
    for spec in CANONICAL_FIELDS:
        match = None
        for synonym in spec.synonyms:
            col = normalized.get(synonym)
            if col and col not in used:
                match = col
                break
        mapping[spec.name] = match
        if match:
            used.add(match)
    return mapping


# ------------------------------------------------------------------------ validate

DateOrder = Literal["auto", "day_first", "month_first"]
_NUMBER_CLEAN = re.compile(r"[\s$€£¥₹,]")


@dataclass
class ValidationResult:
    parsed: ParsedFile
    mapping: dict[str, str | None]
    valid: pd.DataFrame  # canonical columns + id, text_normalized, text_hash, row_number
    rejected: pd.DataFrame  # row_number, reason, + original columns
    warnings: list[str]
    missing_dates: int = 0

    @property
    def n_valid(self) -> int:
        return len(self.valid)

    @property
    def n_rejected(self) -> int:
        return len(self.rejected)


def validate_mapping(mapping: dict[str, str | None], headers: list[str]) -> list[str]:
    errors = []
    if not mapping.get("feedback_text"):
        errors.append("Map a column to **Feedback text** — it is required.")
    chosen = [c for c in mapping.values() if c]
    for col in chosen:
        if col not in headers:
            errors.append(f"Mapped column '{col}' is not in the file.")
    dupes = {c for c in chosen if chosen.count(c) > 1}
    if dupes:
        errors.append(f"Each file column can be mapped once; reused: {', '.join(sorted(dupes))}.")
    return errors


def _parse_number(value: str) -> float | None:
    cleaned = _NUMBER_CLEAN.sub("", value)
    if not cleaned:
        return None
    return float(cleaned)  # raises ValueError


def validate_rows(
    parsed: ParsedFile,
    mapping: dict[str, str | None],
    settings: Settings,
    date_order: DateOrder = "auto",
    now: datetime | None = None,
) -> ValidationResult:
    errors = validate_mapping(mapping, parsed.headers)
    if errors:
        raise ImportValidationError(" ".join(errors))

    now = now or datetime.now()
    idx = {h: i for i, h in enumerate(parsed.headers)}

    def col(row: list[str], name: str) -> str:
        header = mapping.get(name)
        return row[idx[header]].strip() if header else ""

    raw_dates = [col(r, "created_at") for r in parsed.rows]
    parsed_dates = pd.Series([pd.NaT] * len(raw_dates), dtype="datetime64[ns]")
    if mapping.get("created_at"):
        series = pd.Series(raw_dates).replace("", None)
        dayfirst = date_order == "day_first"
        parsed_dates = pd.to_datetime(series, errors="coerce", format="mixed", dayfirst=dayfirst, utc=True)
        parsed_dates = parsed_dates.dt.tz_convert(None)

    valid_records, rejected_records = [], []
    for m in parsed.malformed:
        rejected_records.append({"row_number": m["row_number"], "reason": m["reason"], "raw": m["raw"]})

    warnings_count = {"rating": 0, "value": 0, "truncated": 0}
    missing_dates = 0
    seen_ids: dict[str, int] = {}
    future_limit = now + timedelta(days=1)

    for i, row in enumerate(parsed.rows):
        line = parsed.row_numbers[i]
        reasons: list[str] = []
        original = row[idx[mapping["feedback_text"]]]
        normalized = normalize_text(original)
        if not normalized:
            reasons.append("Feedback text is empty.")
        elif len(normalized) > settings.max_text_chars:
            reasons.append(f"Feedback text exceeds {settings.max_text_chars:,} characters.")
        elif not re.search(r"[^\W\d_]", normalized):
            reasons.append("Feedback text contains no letters.")

        created: datetime | None = None
        raw_date = raw_dates[i]
        if raw_date:
            ts = parsed_dates.iloc[i]
            if pd.isna(ts):
                reasons.append(f"Unrecognised date '{raw_date[:40]}'.")
            elif ts.to_pydatetime() > future_limit:
                reasons.append(f"Date '{raw_date[:40]}' is in the future.")
            elif ts.year < 1990:
                reasons.append(f"Date '{raw_date[:40]}' is implausibly old.")
            else:
                created = ts.to_pydatetime().replace(microsecond=0)
        else:
            missing_dates += 1

        rating = None
        if raw := col(row, "rating"):
            try:
                rating = _parse_number(raw)
                if rating is not None and not (0 <= rating <= 10):
                    rating, warnings_count["rating"] = None, warnings_count["rating"] + 1
            except ValueError:
                warnings_count["rating"] += 1
        value = None
        if raw := col(row, "customer_value"):
            try:
                value = _parse_number(raw)
                if value is not None and value < 0:
                    value, warnings_count["value"] = None, warnings_count["value"] + 1
            except ValueError:
                warnings_count["value"] += 1

        def short(name: str, limit: int = MAX_SHORT_FIELD, _row: list[str] = row) -> str | None:
            v = normalize_text(col(_row, name))
            if len(v) > limit:
                warnings_count["truncated"] += 1
                v = v[:limit]
            return v or None

        external_id = short("feedback_id", MAX_ID_FIELD)
        source = short("source")
        customer_id = short("customer_id", MAX_ID_FIELD)

        if reasons:
            rejected_records.append(
                {"row_number": line, "reason": " ".join(reasons), "raw": parsed.delimiter.join(row)[:500]}
            )
            continue

        fid = stable_feedback_id(external_id, source, normalized, created, customer_id)
        if fid in seen_ids:
            same = "feedback ID" if external_id else "text, date, customer and source"
            rejected_records.append(
                {
                    "row_number": line,
                    "reason": f"Duplicate record: same {same} as row {seen_ids[fid]} in this file.",
                    "raw": parsed.delimiter.join(row)[:500],
                }
            )
            continue
        seen_ids[fid] = line
        valid_records.append(
            {
                "id": fid,
                "row_number": line,
                "external_id": external_id,
                "text_original": original,
                "text_normalized": normalized,
                "text_hash": text_hash(normalized),
                "created_at": created,
                "source": source,
                "customer_id": customer_id,
                "customer_segment": short("customer_segment"),
                "product_area": short("product_area"),
                "rating": rating,
                "customer_value": value,
                "status": short("status", 50),
            }
        )

    warnings = []
    if warnings_count["rating"]:
        warnings.append(f"{warnings_count['rating']} rating value(s) were not numbers in 0–10 and were left blank.")
    if warnings_count["value"]:
        warnings.append(
            f"{warnings_count['value']} customer value(s) were not valid non-negative numbers and were left blank."
        )
    if warnings_count["truncated"]:
        warnings.append(f"{warnings_count['truncated']} metadata value(s) were longer than allowed and were truncated.")
    if mapping.get("created_at") and missing_dates:
        warnings.append(f"{missing_dates} row(s) have no date; they are imported but excluded from trend analysis.")
    if not mapping.get("created_at"):
        warnings.append("No date column is mapped — trend and emerging-problem analysis will be unavailable.")

    valid = pd.DataFrame(valid_records)
    rejected = pd.DataFrame(rejected_records, columns=["row_number", "reason", "raw"]).sort_values("row_number")
    return ValidationResult(parsed, dict(mapping), valid, rejected.reset_index(drop=True), warnings, missing_dates)


# -------------------------------------------------------------------------- import


@dataclass
class ImportSummary:
    batch_id: int
    total_rows: int
    valid_rows: int
    inserted: int
    already_imported: int
    rejected: int
    flagged_text_duplicates: int
    warnings: list[str]


def import_validated(session: Session, result: ValidationResult, is_sample: bool = False) -> ImportSummary:
    parsed = result.parsed
    batch = ImportBatch(
        filename=parsed.filename[:255],
        file_sha256=parsed.sha256,
        encoding=parsed.encoding,
        column_mapping={k: v for k, v in result.mapping.items() if v},
        total_rows=len(parsed.rows) + len(parsed.malformed),
        valid_rows=result.n_valid,
        rejected_rows=result.n_rejected,
        warning_count=len(result.warnings),
        is_sample=is_sample,
    )
    session.add(batch)
    session.flush()

    records = result.valid.to_dict("records") if result.n_valid else []
    ids = [r["id"] for r in records]
    existing: set[str] = set()
    for start in range(0, len(ids), 500):
        chunk = ids[start : start + 500]
        existing.update(session.scalars(select(Feedback.id).where(Feedback.id.in_(chunk))))

    new_records = [r for r in records if r["id"] not in existing]
    hashes = list({r["text_hash"] for r in new_records})
    first_by_hash: dict[str, str] = {}
    for start in range(0, len(hashes), 500):
        chunk = hashes[start : start + 500]
        rows = session.execute(
            select(Feedback.text_hash, Feedback.id)
            .where(Feedback.text_hash.in_(chunk))
            .order_by(Feedback.imported_at, Feedback.id)
        )
        for h, fid in rows:
            first_by_hash.setdefault(h, fid)

    flagged = 0
    for r in new_records:
        dup_of = first_by_hash.get(r["text_hash"])
        if dup_of:
            flagged += 1
        else:
            first_by_hash[r["text_hash"]] = r["id"]
        session.add(
            Feedback(
                id=r["id"],
                external_id=r["external_id"],
                text_original=r["text_original"],
                text_normalized=r["text_normalized"],
                text_hash=r["text_hash"],
                created_at=None if pd.isna(r["created_at"]) else r["created_at"],
                source=r["source"],
                customer_id=r["customer_id"],
                customer_segment=r["customer_segment"],
                product_area=r["product_area"],
                rating=None if pd.isna(r["rating"]) else r["rating"],
                customer_value=None if pd.isna(r["customer_value"]) else r["customer_value"],
                status=r["status"],
                duplicate_of_id=dup_of,
                import_batch_id=batch.id,
            )
        )
        if len(session.new) >= 1000:
            session.flush()

    batch.inserted_rows = len(new_records)
    batch.already_imported_rows = len(records) - len(new_records)
    audit.record(
        session,
        "import",
        "import_batch",
        batch.id,
        f"Imported {batch.inserted_rows} new record(s) from {parsed.filename} "
        f"({batch.already_imported_rows} already present, {batch.rejected_rows} rejected).",
        {"filename": parsed.filename, "mapping": batch.column_mapping},
    )
    session.flush()
    return ImportSummary(
        batch.id,
        batch.total_rows,
        batch.valid_rows,
        batch.inserted_rows,
        batch.already_imported_rows,
        batch.rejected_rows,
        flagged,
        result.warnings,
    )


def import_csv(
    session: Session,
    data: bytes,
    filename: str,
    settings: Settings,
    mapping: dict[str, str | None] | None = None,
    date_order: DateOrder = "auto",
    is_sample: bool = False,
) -> tuple[ImportSummary, ValidationResult]:
    """Convenience: parse, auto-map (unless a mapping is given), validate and import."""
    parsed = read_csv_bytes(data, filename, settings)
    mapping = mapping or suggest_mapping(parsed.headers)
    result = validate_rows(parsed, mapping, settings, date_order)
    return import_validated(session, result, is_sample=is_sample), result
