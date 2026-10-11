"""Workspace lifecycle: sample data, batch deletion and full reset."""

from __future__ import annotations

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from productpulse.config import SAMPLE_DIR, Settings
from productpulse.db.models import Base, Feedback, ImportBatch, SchemaInfo
from productpulse.ingestion.csv_import import ImportSummary, import_csv
from productpulse.services import audit

SAMPLE_FILE = SAMPLE_DIR / "synthetic_feedback.csv"
SMALL_SAMPLE_FILE = SAMPLE_DIR / "sample_feedback.csv"


def is_empty(session: Session) -> bool:
    return (session.scalar(select(func.count()).select_from(Feedback)) or 0) == 0


def load_sample(session: Session, settings: Settings) -> ImportSummary:
    if not SAMPLE_FILE.exists():
        raise FileNotFoundError(f"Sample dataset missing: {SAMPLE_FILE.name}. Run scripts/generate_synthetic_data.py.")
    summary, _ = import_csv(session, SAMPLE_FILE.read_bytes(), SAMPLE_FILE.name, settings, is_sample=True)
    return summary


def import_batches(session: Session) -> list[ImportBatch]:
    return list(session.scalars(select(ImportBatch).order_by(ImportBatch.created_at.desc(), ImportBatch.id.desc())))


def delete_batch(session: Session, batch_id: int) -> int:
    batch = session.get(ImportBatch, batch_id)
    if batch is None:
        return 0
    ids = list(session.scalars(select(Feedback.id).where(Feedback.import_batch_id == batch_id)))
    # Clear duplicate pointers into the rows being deleted first (portable across SQLite/Postgres).
    for start in range(0, len(ids), 500):
        chunk = ids[start : start + 500]
        for fb in session.scalars(select(Feedback).where(Feedback.duplicate_of_id.in_(chunk))):
            fb.duplicate_of_id = None
    session.flush()
    for start in range(0, len(ids), 500):
        for fb in session.scalars(select(Feedback).where(Feedback.id.in_(ids[start : start + 500]))):
            session.delete(fb)  # ORM cascade removes memberships; FK cascades remove pairs/evidence
    audit.record(
        session,
        "import_deleted",
        "import_batch",
        batch_id,
        f"Deleted import “{batch.filename}” and its {len(ids)} record(s).",
    )
    session.delete(batch)
    return len(ids)


def reset(session: Session) -> None:
    """Delete every row of every table except schema info."""
    for table in reversed(Base.metadata.sorted_tables):
        if table.name != SchemaInfo.__tablename__:
            session.execute(delete(table))
    audit.record(session, "workspace_reset", "workspace", "", "Workspace reset: all data deleted.")
