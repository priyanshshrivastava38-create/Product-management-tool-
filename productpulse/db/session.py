"""Engine creation, schema initialisation and transactional sessions.

Schema strategy: ``init_db`` runs ``metadata.create_all`` (idempotent) and
records ``SCHEMA_VERSION`` in ``schema_info``. A mismatch raises a clear error
instead of silently running against an incompatible schema. See
docs/ARCHITECTURE.md ("Schema management") for the upgrade path.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import Engine, create_engine, event, select
from sqlalchemy.orm import Session, sessionmaker

from productpulse.db.models import SCHEMA_VERSION, Base, SchemaInfo


class SchemaVersionError(RuntimeError):
    pass


def make_engine(url: str) -> Engine:
    if url.startswith("sqlite"):
        path = url.replace("sqlite:///", "", 1)
        if path and path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        engine = create_engine(url, connect_args={"check_same_thread": False, "timeout": 30})

        @event.listens_for(engine, "connect")
        def _sqlite_pragmas(dbapi_conn, _record):  # pragma: no cover - trivial
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA foreign_keys=ON")
            if path != ":memory:":
                cur.execute("PRAGMA journal_mode=WAL")
            cur.close()

        return engine
    return create_engine(url, pool_pre_ping=True, pool_size=5, max_overflow=5)


def init_db(engine: Engine) -> None:
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        info = session.scalar(select(SchemaInfo).limit(1))
        if info is None:
            session.add(SchemaInfo(version=SCHEMA_VERSION))
            session.commit()
        elif info.version != SCHEMA_VERSION:
            raise SchemaVersionError(
                f"Database schema version {info.version} does not match application "
                f"version {SCHEMA_VERSION}. Reset the workspace or migrate the database."
            )


class Database:
    """Small wrapper bundling an engine and a session factory."""

    def __init__(self, url: str):
        self.url = url
        self.engine = make_engine(url)
        init_db(self.engine)
        self._factory = sessionmaker(self.engine, expire_on_commit=False)

    @contextmanager
    def session(self) -> Iterator[Session]:
        """Transactional scope: commit on success, roll back on any error."""
        session = self._factory()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()
