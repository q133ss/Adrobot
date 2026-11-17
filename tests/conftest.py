from __future__ import annotations

import os

os.environ.setdefault("AUTO_CREATE_SCHEMA", "false")
os.environ.setdefault("KEITARO_DEMO", "false")
os.environ.setdefault("DATABASE_URL", "sqlite:///./adrobot-test.db")

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base, get_session
from app.deps import get_client
from app.keitaro.client import KeitaroClient
from app.main import create_app
from app.services.audit import ApiCallRecorder
from tools.fake_keitaro.core import FakeTracker, mock_transport


@pytest.fixture
def tracker() -> FakeTracker:
    return FakeTracker()


@pytest.fixture
def session_factory() -> sessionmaker[Session]:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False, class_=Session)


@pytest.fixture
def session(session_factory: sessionmaker[Session]):
    with session_factory() as db_session:
        yield db_session


@pytest.fixture
def keitaro(tracker: FakeTracker) -> KeitaroClient:
    client = KeitaroClient(
        "http://fake-keitaro",
        "demo-api-key",
        transport=mock_transport(tracker),
        max_log_body=200,
    )
    yield client
    client.close()


@pytest.fixture
def web(tracker: FakeTracker, session_factory: sessionmaker[Session]):
    app = create_app()

    def override_session():
        db_session = session_factory()
        try:
            yield db_session
        finally:
            db_session.close()

    def override_client():
        db_session = session_factory()
        client = KeitaroClient(
            "http://fake-keitaro",
            "demo-api-key",
            transport=mock_transport(tracker),
            audit=ApiCallRecorder(db_session),
        )
        try:
            yield client
        finally:
            client.close()
            db_session.close()

    app.dependency_overrides[get_session] = override_session
    app.dependency_overrides[get_client] = override_client

    with TestClient(app) as test_client:
        yield test_client
