from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect

from backend.db_models import Base


def test_alembic_migrations_create_the_current_schema(
    tmp_path: Path, monkeypatch
) -> None:
    database_path = tmp_path / "migration-test.db"
    database_url = f"sqlite+pysqlite:///{database_path.as_posix()}"
    monkeypatch.setenv("DATABASE_URL", database_url)
    project_root = Path(__file__).resolve().parents[1]

    command.upgrade(Config(str(project_root / "alembic.ini")), "head")

    engine = create_engine(database_url)
    try:
        assert set(Base.metadata.tables).issubset(inspect(engine).get_table_names())
        assert "alembic_version" in inspect(engine).get_table_names()
    finally:
        engine.dispose()
