from collections.abc import Generator
import os
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.testclient import TestClient

from backend.api import app, get_session
from backend.database import create_database_engine, create_session_factory
from backend.db_models import FacilityModel, IdempotencyRecordModel


@pytest.mark.skipif(
    os.getenv("RUN_POSTGRES_TESTS") != "1",
    reason="Set RUN_POSTGRES_TESTS=1 to run integration tests against local PostgreSQL.",
)
def test_postgresql_api_idempotency_replays_a_facility_creation() -> None:
    """Exercise FastAPI, reliability middleware, SQLAlchemy, and PostgreSQL together."""

    engine = create_database_engine()
    assert engine.dialect.name == "postgresql"
    session_factory = create_session_factory(engine)
    key = f"postgres-api-{uuid4().hex}"
    facility_id = None
    original_idempotency_session_factory = app.state.idempotency_session_factory

    def override_get_session() -> Generator[Session, None, None]:
        session = session_factory()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    app.dependency_overrides[get_session] = override_get_session
    app.state.idempotency_session_factory = session_factory
    try:
        with TestClient(app) as client:
            payload = {
                "name": f"PostgreSQL Integration Facility {key[-8:]}",
                "shipping_address": "1 Integration Road",
            }
            headers = {"Idempotency-Key": key}
            first = client.post("/facilities", json=payload, headers=headers)
            replay = client.post("/facilities", json=payload, headers=headers)

            assert first.status_code == 201
            assert replay.status_code == 201
            assert replay.json()["id"] == first.json()["id"]
            assert replay.headers["X-Idempotency-Replayed"] == "true"
            facility_id = first.json()["id"]
    finally:
        app.dependency_overrides.clear()
        app.state.idempotency_session_factory = original_idempotency_session_factory
        with session_factory.begin() as session:
            if facility_id is not None:
                facility = session.get(FacilityModel, facility_id)
                if facility is not None:
                    session.delete(facility)
            record = session.scalar(
                select(IdempotencyRecordModel).where(IdempotencyRecordModel.key == key)
            )
            if record is not None:
                session.delete(record)
        engine.dispose()
