import json
from datetime import UTC, datetime, timedelta, timezone

import pytest

from mule_triage.contracts import MAX_AMOUNT_CENTS, RulesConfig
from mule_triage.ingest import Rejection, parse_payments, parse_profiles
from mule_triage.store import FileTally, RunId, SqliteStore, open_store, utc_text

AS_OF = datetime(2026, 11, 3, tzinfo=UTC)
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


def save(store: SqliteStore, payments: list[str], profiles: list[str]) -> RunId:
    with store.transaction():
        return store.save(AS_OF, parse_payments(payments), parse_profiles(profiles))


def test_tallies_a_run_from_its_stored_rows() -> None:
    payments = [
        payment_line(),
        payment_line(event_time="2026-11-02T13:10:00"),
        payment_line(),
    ]
    with open_store(":memory:") as store:
        run_id = save(store, payments, [profile_line()])

        tally = store.tally(run_id)

    assert tally.payments == FileTally(
        received=3,
        accepted=1,
        rejections=(Rejection(2, "TX-101-02", "event_time: timezone_aware"),),
        duplicate_ids=("TX-101-02",),
    )
    assert tally.profiles == FileTally(1, 1, (), ())


def test_records_an_exact_repeat_of_a_stored_record_as_a_duplicate() -> None:
    with open_store(":memory:") as store:
        save(store, [payment_line()], [profile_line()])
        second = save(store, [payment_line()], [profile_line()])

        tally = store.tally(second)

    assert tally.payments == FileTally(1, 0, (), ("TX-101-02",))
    assert tally.profiles == FileTally(1, 0, (), ("ACC-101",))


def test_rejects_a_changed_payment_under_a_stored_id() -> None:
    with open_store(":memory:") as store:
        save(store, [payment_line()], [])
        second = save(store, [payment_line(amount_cents=90001)], [])

        tally = store.tally(second)

    assert tally.payments == FileTally(
        1, 0, (Rejection(1, "TX-101-02", "transaction_id: changed_since_stored"),), ()
    )


def test_rejects_a_changed_profile_under_a_stored_id() -> None:
    with open_store(":memory:") as store:
        save(store, [], [profile_line()])
        second = save(store, [], [profile_line(opened_at="2020-01-01T00:00:00Z")])

        tally = store.tally(second)

    assert tally.profiles == FileTally(
        1, 0, (Rejection(1, "ACC-101", "account_id: changed_since_stored"),), ()
    )


def test_a_payment_may_name_an_account_with_no_profile() -> None:
    with open_store(":memory:") as store:
        run_id = save(store, [payment_line(to_account="ACC-999")], [])

        tally = store.tally(run_id)

    assert tally.payments.accepted == 1


def test_stores_the_largest_amount_a_payment_may_have() -> None:
    with open_store(":memory:") as store:
        run_id = save(store, [payment_line(amount_cents=MAX_AMOUNT_CENTS)], [])

        tally = store.tally(run_id)

    assert tally.payments.accepted == 1


def test_reads_back_every_stored_payment_and_profile_as_parsed() -> None:
    payments = parse_payments([payment_line()])
    profiles = parse_profiles([profile_line()])
    with open_store(":memory:") as store:
        with store.transaction():
            store.save(AS_OF, payments, profiles)

        stored = (store.stored_payments(), store.stored_profiles())

    assert stored == (
        (payments.accepted[0].record,),
        (profiles.accepted[0].record,),
    )


def test_storing_outcomes_for_a_run_that_isnt_in_the_ledger_raises(
    rules: RulesConfig,
) -> None:
    with open_store(":memory:") as store:
        with pytest.raises(LookupError, match="run 7"):
            store.save_outcomes(RunId(7), rules, ())


def test_tallying_a_run_that_isnt_in_the_ledger_raises() -> None:
    with open_store(":memory:") as store:
        with pytest.raises(LookupError, match="run 7"):
            store.tally(RunId(7))


def test_converts_a_time_to_utc_text_with_microseconds() -> None:
    moment = datetime(2026, 11, 2, 20, 25, tzinfo=timezone(timedelta(hours=11)))

    text = utc_text(moment)

    assert text == "2026-11-02T09:25:00.000000+00:00"


def test_refuses_to_store_a_time_without_a_timezone() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        utc_text(datetime(2026, 11, 2, 9, 25))
