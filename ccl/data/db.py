"""Database setup.

The reference stack targets PostgreSQL + pgvector. This milestone contains no
retrieval or embeddings, so it runs on SQLite for a fast, dependency-free test
suite. The tenant-isolation, immutability, and append-only invariants proven
here are storage-agnostic and carry over to Postgres unchanged.
"""

from __future__ import annotations

from sqlalchemy import Integer, create_engine, select
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker


class Base(DeclarativeBase):
    pass


class SchemaMeta(Base):
    """Single-row table holding the schema/seed version. Replaces the SQLite-only
    PRAGMA user_version so the version guard works identically on Postgres."""

    __tablename__ = "schema_meta"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)  # always 1
    version: Mapped[int] = mapped_column(Integer, nullable=False)


def make_engine(url: str = "sqlite+pysqlite:///:memory:"):
    engine = create_engine(url, echo=False, future=True)
    return engine


def make_session_factory(engine):
    return sessionmaker(bind=engine, expire_on_commit=False, future=True)


def init_db(engine) -> None:
    # Import models so their tables register on Base.metadata before create_all.
    from . import models  # noqa: F401
    from . import tutor_models  # noqa: F401
    from . import insight_models  # noqa: F401
    from . import privacy_models  # noqa: F401
    from . import provider_models  # noqa: F401

    Base.metadata.create_all(engine)


def read_schema_version(session) -> int | None:
    """Stored schema version, or None if never stamped (a fresh DB)."""
    row = session.get(SchemaMeta, 1)
    return row.version if row else None


def stamp_schema_version(session, version: int) -> None:
    row = session.get(SchemaMeta, 1)
    if row is None:
        session.add(SchemaMeta(id=1, version=version))
    else:
        row.version = version
    session.flush()


def reset_schema(engine) -> None:
    """Drop and recreate all tables (used on a version mismatch). Works on any
    dialect, unlike deleting a SQLite file."""
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
