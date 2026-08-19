"""Small V0 reliability primitives for HTTP requests and database operations."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
import logging
import os
from time import sleep
from typing import TypeVar

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session, sessionmaker

from .db_models import IdempotencyRecordModel, IdempotencyStatus


logger = logging.getLogger("residential_clothing")
T = TypeVar("T")
_TRANSIENT_SQLSTATES = {"40001", "40P01"}  # serialization failure, deadlock


def configure_application_logging() -> None:
    """Configure concise local logs without adding an external logging service."""

    if logger.handlers:
        return

    handler = logging.StreamHandler()
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s")
    )
    logger.addHandler(handler)
    logger.setLevel(os.getenv("LOG_LEVEL", "INFO").upper())
    logger.propagate = False


def is_retryable_database_error(error: OperationalError) -> bool:
    """Return whether PostgreSQL identified the failure as safe to retry."""

    sqlstate = getattr(error.orig, "sqlstate", None) or getattr(error.orig, "pgcode", None)
    return bool(error.connection_invalidated or sqlstate in _TRANSIENT_SQLSTATES)


def retry_transient_database_operation(
    operation: Callable[[], T],
    *,
    operation_name: str,
    max_attempts: int = 3,
    sleep_function: Callable[[float], None] = sleep,
) -> T:
    """Run a small safe operation with bounded exponential retry on transient DB errors."""

    if max_attempts < 1:
        raise ValueError("max_attempts must be at least one")

    for attempt in range(1, max_attempts + 1):
        try:
            return operation()
        except OperationalError as error:
            if not is_retryable_database_error(error) or attempt == max_attempts:
                logger.exception(
                    "database_operation_failed operation=%s attempt=%s", operation_name, attempt
                )
                raise

            delay_seconds = 0.05 * (2 ** (attempt - 1))
            logger.warning(
                "database_operation_retry operation=%s attempt=%s delay_seconds=%.2f",
                operation_name,
                attempt,
                delay_seconds,
            )
            sleep_function(delay_seconds)

    raise RuntimeError("Unreachable retry state")


class IdempotencyConflictError(Exception):
    """The same key was reused for a different request."""


class IdempotencyInProgressError(Exception):
    """Another request with the same key is still executing."""


@dataclass(frozen=True)
class IdempotencyReplay:
    status_code: int
    body: str
    content_type: str | None


class IdempotencyRepository:
    """Store and replay POST responses using a client-supplied idempotency key."""

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def begin(self, *, key: str, method: str, path: str, request_hash: str) -> IdempotencyReplay | None:
        """Start a request or return its previously completed response."""

        try:
            with self._session_factory.begin() as session:
                record = session.scalar(
                    select(IdempotencyRecordModel).where(IdempotencyRecordModel.key == key)
                )
                if record is None:
                    session.add(
                        IdempotencyRecordModel(
                            key=key,
                            method=method,
                            path=path,
                            request_hash=request_hash,
                            status=IdempotencyStatus.PROCESSING.value,
                        )
                    )
                    return None
                return self._decision_for_existing(record, method, path, request_hash)
        except IntegrityError:
            # A concurrent request inserted the unique key first. Read its state.
            with self._session_factory() as session:
                record = session.scalar(
                    select(IdempotencyRecordModel).where(IdempotencyRecordModel.key == key)
                )
                if record is None:
                    raise
                return self._decision_for_existing(record, method, path, request_hash)

    def complete(
        self,
        *,
        key: str,
        response_status: int,
        response_body: str,
        content_type: str | None,
    ) -> None:
        """Persist the final response, including a durable failure state for non-2xx results."""

        with self._session_factory.begin() as session:
            record = session.scalar(
                select(IdempotencyRecordModel)
                .where(IdempotencyRecordModel.key == key)
                .with_for_update()
            )
            if record is None:
                logger.error("idempotency_record_missing key=%s", key)
                return

            succeeded = 200 <= response_status < 300
            record.status = (
                IdempotencyStatus.SUCCEEDED.value if succeeded else IdempotencyStatus.FAILED.value
            )
            record.response_status = response_status
            record.response_body = response_body
            record.content_type = content_type
            record.failure_detail = None if succeeded else response_body[:2000]
            record.completed_at = datetime.now(timezone.utc)

    @staticmethod
    def _decision_for_existing(
        record: IdempotencyRecordModel, method: str, path: str, request_hash: str
    ) -> IdempotencyReplay:
        if (
            record.method != method
            or record.path != path
            or record.request_hash != request_hash
        ):
            raise IdempotencyConflictError("Idempotency-Key was already used for another request")
        if record.status == IdempotencyStatus.PROCESSING.value:
            raise IdempotencyInProgressError("A request with this Idempotency-Key is still processing")

        if record.response_status is None or record.response_body is None:
            raise RuntimeError("Completed idempotency record has no saved response")
        return IdempotencyReplay(
            status_code=record.response_status,
            body=record.response_body,
            content_type=record.content_type,
        )
