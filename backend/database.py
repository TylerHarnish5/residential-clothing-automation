"""Database configuration helpers for the persistence layer.

Production uses the ``DATABASE_URL`` environment variable. The default is a
local PostgreSQL URL; tests provide a SQLite URL explicitly.
"""

from __future__ import annotations

import os

from dotenv import load_dotenv
from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

DEFAULT_DATABASE_URL = (
    "postgresql+psycopg://postgres:postgres@localhost:5432/residential_clothing"
)

# Loads local development settings when this module is imported. Existing
# environment variables take precedence over values in .env.
load_dotenv()


def get_database_url() -> str:
    """Return the configured database URL without connecting to it."""

    return os.getenv("DATABASE_URL", DEFAULT_DATABASE_URL)


def create_database_engine(database_url: str | None = None) -> Engine:
    """Create an SQLAlchemy engine for PostgreSQL or a test database URL."""

    return create_engine(database_url or get_database_url())


def create_session_factory(engine: Engine) -> sessionmaker[Session]:
    """Create application database sessions bound to an existing engine."""

    return sessionmaker(bind=engine, expire_on_commit=False)
