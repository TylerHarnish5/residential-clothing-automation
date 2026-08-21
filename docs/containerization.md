# Containerization (Milestone 9)

The project can run locally as two Docker Compose services:

- `db`: PostgreSQL 16 with a named volume for local development data.
- `app`: the FastAPI application and its operational frontend.

The application waits for PostgreSQL's health check, applies the existing
Alembic migrations, and then starts Uvicorn. It is available at
`http://127.0.0.1:8000/`; API documentation remains at `/docs`.

GitHub Actions also builds the application image on each push and pull request.

## Start the stack

Install and start Docker Desktop, then run this from the repository root:

```powershell
docker compose up --build
```

The first startup downloads the Python and PostgreSQL images. The default
database is named `residential_clothing`. It uses a local-only default password
so the stack can start without using the repository's `.env` database URL.

To choose a different local Compose password before the first startup:

```powershell
$env:POSTGRES_PASSWORD = "choose-a-local-development-password"
docker compose up --build
```

The application connects to PostgreSQL inside the Compose network. PostgreSQL
is also available from the host at port `5433`, intentionally avoiding the
usual local PostgreSQL port `5432`.

## Stop and inspect

```powershell
docker compose ps
docker compose logs -f app
docker compose down
```

`docker compose down` stops the services but keeps the `postgres_data` named
volume. To permanently remove the containerized database data, use the
destructive command below only when you intend to start fresh:

```powershell
docker compose down -v
```

## V0 boundary

This is a local development and portfolio setup. It does not add a registry,
production image publishing, cloud deployment, or secret-management service.
