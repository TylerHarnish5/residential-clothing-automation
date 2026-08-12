"""Verify that the configured PostgreSQL database can persist a product.

Run from the repository root with ``python scripts/verify_postgresql.py``.
First apply the versioned schema with ``python -m alembic upgrade head``.
The script intentionally leaves its uniquely identified verification product
in the database.
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from sys import path
from uuid import uuid4

from sqlalchemy import inspect

path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.database import create_database_engine, create_session_factory
from backend.db_models import ProductModel
from backend.orders import Product
from backend.repositories import ProductRepository


def main() -> None:
    engine = create_database_engine()
    if engine.dialect.name != "postgresql":
        raise RuntimeError("DATABASE_URL must point to PostgreSQL for this verification")
    if "products" not in inspect(engine).get_table_names(schema="public"):
        raise RuntimeError("Database schema is missing; run 'python -m alembic upgrade head'")
    session_factory = create_session_factory(engine)
    sample_sku = f"VERIFY-{uuid4().hex[:12].upper()}"
    expected = Product(sample_sku, "Database Verification Shirt", "M", "19.99")

    with session_factory.begin() as session:
        saved = ProductRepository().add(session, expected)
        saved_id = saved.id

    with session_factory() as session:
        retrieved = session.get(ProductModel, saved_id)
        if retrieved is None:
            raise RuntimeError("Committed product could not be retrieved")

        assert retrieved.sku == expected.sku
        assert retrieved.name == expected.name
        assert retrieved.size == expected.size
        assert retrieved.unit_price == Decimal("19.99")
        assert retrieved.is_active is True

    print(f"PostgreSQL persistence verified: {sample_sku} ({saved_id})")


if __name__ == "__main__":
    main()
