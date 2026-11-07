import json

import pytest

from mule_triage.records.ingest import Rejection, parse_payments, parse_profiles

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
    return json.dumps({**PAYMENT, **changes}) + "\n"


def profile_line(**changes: object) -> str:
    return json.dumps({**PROFILE, **changes}) + "\n"


def test_accepts_valid_lines_and_counts_every_line_received() -> None:
    lines = [payment_line(), payment_line(transaction_id="TX-101-03"), "\n"]

    parsed = parse_payments(lines)

    assert parsed.received == 3
    assert [accepted.line_number for accepted in parsed.accepted] == [1, 2]
    assert [rejection.line_number for rejection in parsed.rejections] == [3]


def test_rejects_a_time_without_a_timezone_with_its_line_id_and_reason() -> None:
    lines = [payment_line(), payment_line(event_time="2026-11-02T13:10:00")]

    parsed = parse_payments(lines)

    assert parsed.rejections == (
        Rejection(2, "TX-101-02", "event_time: timezone_aware"),
    )


@pytest.mark.parametrize(
    ("line", "value"),
    [
        (payment_line(event_time="2026-11-02T13:10:00"), "2026-11-02T13:10:00"),
        (payment_line(amount_cents="90000"), "90000"),
        (payment_line(sender_name="Alex Example"), "Alex Example"),
        (payment_line(**{"Alex Example": 1}), "Alex Example"),
        ("Alex Example sent $900\n", "Alex Example"),
    ],
    ids=[
        "time without a timezone",
        "amount as text",
        "name field",
        "name as a key",
        "not JSON",
    ],
)
def test_a_rejected_payment_carries_a_reason_that_doesnt_quote_its_value(
    line: str, value: str
) -> None:
    parsed = parse_payments([line])

    [rejection] = parsed.rejections
    assert rejection.reason
    assert value not in rejection.reason


@pytest.mark.parametrize(
    ("line", "value"),
    [
        (profile_line(opened_at="2020-01-01T00:00:00"), "2020-01-01T00:00:00"),
        (profile_line(holder_name="Sam Example"), "Sam Example"),
        (profile_line(knowledge_time=1793610300), "1793610300"),
    ],
    ids=["time without a timezone", "name field", "time as a number"],
)
def test_a_rejected_profile_carries_a_reason_that_doesnt_quote_its_value(
    line: str, value: str
) -> None:
    parsed = parse_profiles([line])

    [rejection] = parsed.rejections
    assert rejection.reason
    assert value not in rejection.reason


def test_names_a_key_that_isnt_a_field_only_as_an_unexpected_field() -> None:
    line = profile_line(holder_name="Sam Example")

    parsed = parse_profiles([line])

    assert parsed.rejections[0].reason == "unexpected field: extra_forbidden"


def test_a_line_that_isnt_json_is_rejected_without_an_id() -> None:
    line = "not json\n"

    parsed = parse_profiles([line])

    assert parsed.rejections == (Rejection(1, None, "record: json_invalid"),)


def test_a_number_too_long_for_python_to_read_rejects_only_its_line() -> None:
    line = '{"transaction_id": "TX-101-02", "reference": ' + "9" * 5000 + "}\n"

    parsed = parse_payments([line, payment_line()])

    assert [rejection.record_id for rejection in parsed.rejections] == [None]
    assert len(parsed.accepted) == 1


def test_an_id_that_isnt_text_isnt_reported_as_the_record_id() -> None:
    line = payment_line(transaction_id=101)

    parsed = parse_payments([line])

    assert parsed.rejections == (Rejection(1, None, "transaction_id: string_type"),)
