"""Engine/session setup. SQLite by default (single-user prototype per the
PRD); point DATABASE_URL at Postgres when concurrency/deployment needs grow
-- nothing else in the codebase should need to change."""
from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from polytrader.core.config import get_settings
from polytrader.core.models import Base

_engine: Engine | None = None
_SessionFactory: sessionmaker[Session] | None = None


def get_engine() -> Engine:
    global _engine
    if _engine is None:
        url = get_settings().database_url
        connect_args = {"check_same_thread": False} if url.startswith("sqlite") else {}
        _engine = create_engine(url, connect_args=connect_args)
    return _engine


def init_db() -> None:
    """Create all tables if they don't already exist. No migration
    framework yet -- fine for the SQLite prototype; swap in Alembic before
    Postgres/production."""
    Base.metadata.create_all(get_engine())


def get_session_factory() -> sessionmaker[Session]:
    global _SessionFactory
    if _SessionFactory is None:
        _SessionFactory = sessionmaker(bind=get_engine(), expire_on_commit=False)
    return _SessionFactory


@contextmanager
def session_scope() -> Iterator[Session]:
    session = get_session_factory()()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
