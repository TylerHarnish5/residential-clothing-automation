# Continuous Integration (Milestone 8)

GitHub Actions runs the repository checks for every push and pull request.
It can also be started manually from the repository's **Actions** tab.

## What CI verifies

1. **Lint Python:** Ruff checks the application and test code for import,
   syntax, and common error issues.
2. **Build container image:** Docker builds the application image, confirming
   that the Dockerfile is valid.
3. **Test with PostgreSQL:** CI starts a temporary PostgreSQL 16 service,
   applies the Alembic migrations, runs the full test suite with
   `RUN_POSTGRES_TESTS=1`, and verifies that no migration is missing.

The PostgreSQL service is created only for that GitHub Actions run and is
discarded afterwards. It does not use the local `.env` file or any real
database password.

## Run the same checks locally

```powershell
python -m pip install -r requirements-dev.txt
python -m ruff check backend tests
$env:RUN_POSTGRES_TESTS = "1"
python -m pytest -q
python -m alembic check
```

Your local PostgreSQL database must be running and migrated before the
PostgreSQL-backed tests can run.

## V0 boundary

This workflow provides continuous integration only. It does not deploy the
application, publish artifacts, or manage cloud infrastructure.
