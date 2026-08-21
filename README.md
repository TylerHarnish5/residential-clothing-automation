# Residential Clothing Automation

An end-to-end business-process automation project modeling the order
and fulfillment workflow of a clothing provider serving residential
care facilities.

## Current V0

The project currently includes Python domain rules, PostgreSQL persistence and
migrations, a FastAPI API, workflow automation, reliability safeguards, and a
lightweight operational frontend.

## Continuous integration

GitHub Actions runs Ruff, PostgreSQL-backed tests, and migration checks on
every push and pull request. See [CI documentation](docs/ci.md) for details
and local commands.

## Containerized local setup

Docker Compose can start the FastAPI application and a separate local
PostgreSQL database with one command. See [containerization documentation](docs/containerization.md).

## AWS deployment preparation

Milestone 10 adds a low-cost AWS deployment plan for one EC2 application
instance and one private RDS PostgreSQL instance. It does not provision AWS
resources automatically. See the [AWS deployment guide](docs/deployment.md)
before creating anything, especially its current Free Plan and cost checks.
