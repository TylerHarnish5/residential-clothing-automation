from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_dockerfile_runs_the_fastapi_application_as_a_non_root_user() -> None:
    dockerfile = (PROJECT_ROOT / "Dockerfile").read_text(encoding="utf-8")

    assert "FROM python:3.12-slim" in dockerfile
    assert "USER appuser" in dockerfile
    assert '"backend.api:app"' in dockerfile
    assert '"0.0.0.0"' in dockerfile


def test_compose_starts_postgresql_before_migrating_and_running_the_app() -> None:
    compose = (PROJECT_ROOT / "compose.yaml").read_text(encoding="utf-8")

    assert "image: postgres:16" in compose
    assert "condition: service_healthy" in compose
    assert "python -m alembic upgrade head" in compose
    assert '"5433:5432"' in compose
    assert "postgres_data:/var/lib/postgresql/data" in compose


def test_dockerignore_excludes_local_secrets_and_build_artifacts() -> None:
    dockerignore = (PROJECT_ROOT / ".dockerignore").read_text(encoding="utf-8")

    assert ".env" in dockerignore
    assert ".git/" in dockerignore
    assert ".pytest_tmp/" in dockerignore


def test_aws_compose_uses_rds_environment_and_explicit_migrations() -> None:
    compose = (PROJECT_ROOT / "compose.aws.yaml").read_text(encoding="utf-8")

    assert "/opt/residential-clothing/app.env" in compose
    assert '"python", "-m", "alembic", "upgrade", "head"' in compose
    assert '"80:8000"' in compose
    assert "image: postgres" not in compose
    assert "DATABASE_URL=" not in compose
