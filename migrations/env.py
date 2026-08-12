"""Alembic environment using the application's configured database URL."""

from __future__ import annotations

from alembic import context
from sqlalchemy import pool

from backend.database import create_database_engine
from backend.db_models import Base


config = context.config
target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Generate SQL without opening a database connection."""

    context.configure(
        url=create_database_engine().url.render_as_string(hide_password=False),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Apply migrations using the configured PostgreSQL connection."""

    connectable = create_database_engine()
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
