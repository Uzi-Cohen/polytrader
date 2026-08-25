import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

import polytrader.core.events as events_module
from polytrader.core.models import Base


@pytest.fixture(autouse=True)
def _reset_event_bus():
    """The event bus is a module-level singleton (core/events.py); reset it
    between tests so a subscriber registered in one test never fires on
    events published by another."""
    events_module._bus = None
    yield
    events_module._bus = None


@pytest.fixture()
def db_session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = Session(bind=engine)
    try:
        yield session
    finally:
        session.close()
        engine.dispose()
