"""Database setup.

The reference stack targets PostgreSQL + pgvector. This milestone contains no
retrieval or embeddings, so it runs on SQLite for a fast, dependency-free test
suite. The tenant-isolation, immutability, and append-only invariants proven
here are storage-agnostic and carry over to Postgres unchanged.
"""

from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker


class Base(DeclarativeBase):
    pass


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
