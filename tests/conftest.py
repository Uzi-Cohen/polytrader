import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from polytrader.core.models import Base


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
