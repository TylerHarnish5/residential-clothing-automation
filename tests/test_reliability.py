import pytest
from sqlalchemy.exc import OperationalError

from backend.reliability import retry_transient_database_operation


class TransientDatabaseError(Exception):
    sqlstate = "40001"


def test_retry_retries_a_transient_database_error_with_bounded_backoff() -> None:
    attempts = 0
    delays: list[float] = []

    def operation() -> str:
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise OperationalError("retry", {}, TransientDatabaseError())
        return "saved"

    result = retry_transient_database_operation(
        operation,
        operation_name="test_operation",
        sleep_function=delays.append,
    )

    assert result == "saved"
    assert attempts == 3
    assert delays == [0.05, 0.1]


def test_retry_does_not_repeat_a_non_transient_database_error() -> None:
    attempts = 0

    def operation() -> None:
        nonlocal attempts
        attempts += 1
        raise OperationalError("no retry", {}, Exception("permanent"))

    with pytest.raises(OperationalError):
        retry_transient_database_operation(operation, operation_name="test_operation")

    assert attempts == 1
