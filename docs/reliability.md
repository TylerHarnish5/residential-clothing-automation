# Reliability (Milestone 6)

## Request logging

The API writes one completion log per request with a request ID, HTTP method,
path, status code, and elapsed time. Clients may provide `X-Request-ID`; the
API generates one when it is absent. Workflow automation also logs how many
approved orders it evaluated and how many fulfillment tasks it created.

## Idempotent POST requests

Clients may include an `Idempotency-Key` header on any `POST` request. The API
stores that key, the request body hash, and the resulting response in
PostgreSQL.

- Repeating the same method, path, key, and body replays the original response
  without performing the operation again.
- Reusing a key with a different path or body returns `409 Conflict`.
- A request already in progress with the same key returns `409 Conflict`.
- A non-2xx response is recorded as `failed` and is replayed consistently for
  that exact request.

The durable record states are `processing`, `succeeded`, and `failed`.

## Retries

The application retries small internal reliability-record database operations
up to three times when PostgreSQL reports a serialization failure, deadlock,
or lost connection. Delays are 0.05 seconds and then 0.10 seconds.

Business operations are not blindly rerun server-side after a transaction
failure. Instead, callers can safely retry a `POST` with the same
`Idempotency-Key`, preventing duplicate products, orders, stock receipts, or
workflow actions.

## V0 boundaries

There is no background queue, external observability service, or automatic
retry worker. Those are intentionally outside this portfolio project's V0
scope. The integration test exercises FastAPI, the reliability middleware,
SQLAlchemy, and the configured local PostgreSQL database together.
