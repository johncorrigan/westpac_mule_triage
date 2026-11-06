import json
from collections.abc import Mapping
from datetime import UTC, datetime

import pytest
from pydantic import BaseModel, ValidationError

from mule_triage.contracts import (
    AccountId,
    AccountProfile,
    AccountResult,
    Alert,
    NeedsReview,
    NoAlert,
    Outcome,
    Payment,
    Priority,
    ReasonCode,
)

PAYMENT = {
    "transaction_id": "TX-101-02",
    "from_account": "ACC-202",
    "to_account": "ACC-101",
    "amount_cents": 90000,
    "event_time": "2026-11-02T09:25:00Z",
    "knowledge_time": "2026-11-02T09:26:00Z",
    "reference": "rent",
}
PROFILE = {
    "account_id": "ACC-101",
    "opened_at": "2026-10-25T00:00:00Z",
    "knowledge_time": "2026-10-25T00:00:00Z",
}


def payment_line(**changes: object) -> str:
    return json.dumps({**PAYMENT, **changes})


def profile_line(**changes: object) -> str:
    return json.dumps({**PROFILE, **changes})


def without(record: Mapping[str, object], field: str) -> str:
    return json.dumps({key: value for key, value in record.items() if key != field})


def test_reads_a_payment_line() -> None:
    line = payment_line()

    payment = Payment.model_validate_json(line)

    assert payment.transaction_id == "TX-101-02"
    assert payment.amount_cents == 90000
    assert payment.event_time == datetime(2026, 11, 2, 9, 25, tzinfo=UTC)


def test_reads_a_profile_line() -> None:
    line = profile_line()

    profile = AccountProfile.model_validate_json(line)

    assert profile.account_id == "ACC-101"
    assert profile.opened_at == datetime(2026, 10, 25, tzinfo=UTC)


def test_converts_a_time_with_an_offset_to_utc() -> None:
    line = payment_line(event_time="2026-11-02T20:25:00+11:00")

    payment = Payment.model_validate_json(line)

    assert payment.event_time.tzinfo is UTC
    assert payment.event_time == datetime(2026, 11, 2, 9, 25, tzinfo=UTC)


@pytest.mark.parametrize(
    ("model", "line"),
    [
        (Payment, payment_line(sender_name="Alex Example")),
        (Payment, payment_line(recipient_name="Sam Example")),
        (AccountProfile, profile_line(holder_name="Alex Example")),
        (AccountProfile, profile_line(account_name="Example Savings")),
    ],
    ids=[
        "payment sender",
        "payment recipient",
        "profile holder",
        "profile account",
    ],
)
def test_rejects_a_name_field(model: type[BaseModel], line: str) -> None:
    with pytest.raises(ValidationError, match="extra_forbidden"):
        model.model_validate_json(line)


@pytest.mark.parametrize(
    ("model", "line"),
    [
        (Payment, payment_line(event_time="2026-11-02T13:10:00")),
        (Payment, payment_line(knowledge_time="2026-11-02T13:11:00")),
        (AccountProfile, profile_line(opened_at="2020-01-01T00:00:00")),
        (AccountProfile, profile_line(knowledge_time="2020-01-01T00:00:00")),
    ],
    ids=[
        "payment event",
        "payment knowledge",
        "profile opened",
        "profile knowledge",
    ],
)
def test_rejects_a_time_without_a_timezone(model: type[BaseModel], line: str) -> None:
    with pytest.raises(ValidationError, match="timezone_aware"):
        model.model_validate_json(line)


def test_rejects_a_time_given_as_a_number_of_seconds() -> None:
    line = payment_line(event_time=1793610300)

    with pytest.raises(ValidationError, match="datetime_type"):
        Payment.model_validate_json(line)


@pytest.mark.parametrize(
    "amount",
    [900.5, 90000.0, "90000", True],
    ids=["fraction of a cent", "float", "string", "boolean"],
)
def test_rejects_an_amount_that_isnt_whole_cents(amount: object) -> None:
    line = payment_line(amount_cents=amount)

    with pytest.raises(ValidationError, match="int_type"):
        Payment.model_validate_json(line)


@pytest.mark.parametrize("amount", [0, -100], ids=["zero", "negative"])
def test_rejects_an_amount_that_isnt_positive(amount: int) -> None:
    line = payment_line(amount_cents=amount)

    with pytest.raises(ValidationError, match="greater_than"):
        Payment.model_validate_json(line)


def test_rejects_an_amount_too_large_for_sqlite_to_store() -> None:
    line = payment_line(amount_cents=2**63)

    with pytest.raises(ValidationError, match="less_than_equal"):
        Payment.model_validate_json(line)


@pytest.mark.parametrize("field", list(PAYMENT))
def test_rejects_a_payment_missing_a_field(field: str) -> None:
    line = without(PAYMENT, field)

    with pytest.raises(ValidationError, match="missing"):
        Payment.model_validate_json(line)


@pytest.mark.parametrize("field", list(PROFILE))
def test_rejects_a_profile_missing_a_field(field: str) -> None:
    line = without(PROFILE, field)

    with pytest.raises(ValidationError, match="missing"):
        AccountProfile.model_validate_json(line)


def test_a_payment_cant_be_changed_once_read() -> None:
    payment = Payment.model_validate_json(payment_line())

    with pytest.raises(ValidationError, match="frozen_instance"):
        payment.amount_cents = 1


ACCOUNT = AccountId("ACC-101")
REASONS = frozenset({ReasonCode.FAN_IN})


@pytest.mark.parametrize(
    ("result", "outcome"),
    [
        (Alert(ACCOUNT, REASONS, Priority.MEDIUM, incomplete=False), Outcome.ALERT),
        (NeedsReview(ACCOUNT, REASONS), Outcome.NEEDS_REVIEW),
        (NoAlert(ACCOUNT, REASONS), Outcome.NO_ALERT),
    ],
    ids=["alert", "needs review", "no alert"],
)
def test_each_account_result_reports_its_outcome(
    result: AccountResult, outcome: Outcome
) -> None:
    reported = result.outcome

    assert reported is outcome
