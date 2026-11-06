from datetime import UTC, datetime, timedelta
from fractions import Fraction

import pytest

from mule_triage.contracts import (
    AccountId,
    AccountProfile,
    Conflict,
    Known,
    Payment,
    ReasonCode,
    RulesConfig,
    SignalValue,
    TransactionId,
    Unknown,
)
from mule_triage.signals import (
    Activity,
    PassThrough,
    Signal,
    account_age_days,
    activities,
    assess,
    distinct_senders,
    pass_through,
)

AS_OF = datetime(2026, 11, 3, tzinfo=UTC)
ACCOUNT = AccountId("ACC-101")
MORNING = datetime(2026, 11, 2, 9, tzinfo=UTC)
OLD = datetime(2020, 1, 1, tzinfo=UTC)


def paid(
    sender: str,
    receiver: str,
    cents: int,
    at: datetime,
    known_at: datetime | None = None,
) -> Payment:
    return Payment(
        transaction_id=TransactionId("TX-1"),
        from_account=AccountId(sender),
        to_account=AccountId(receiver),
        amount_cents=cents,
        event_time=at,
        knowledge_time=known_at or at + timedelta(minutes=1),
        reference="rent",
    )


def opened(
    at: datetime, account: str = ACCOUNT, known_at: datetime | None = None
) -> AccountProfile:
    return AccountProfile(
        account_id=AccountId(account), opened_at=at, knowledge_time=known_at or at
    )


def activity(
    received: tuple[Payment, ...] = (),
    sent: tuple[Payment, ...] = (),
    profile: AccountProfile | None = None,
) -> Activity:
    return Activity(ACCOUNT, AS_OF, received, sent, profile or opened(OLD))


def received_from(*senders: str, cents: int = 100) -> tuple[Payment, ...]:
    return tuple(paid(sender, ACCOUNT, cents, MORNING) for sender in senders)


def breach_of(code: ReasonCode, of: Activity, rules: RulesConfig) -> SignalValue[bool]:
    return {found.code: found.breach for found in assess(of, rules)}[code]


def test_counts_only_payments_known_by_the_as_of_time(rules: RulesConfig) -> None:
    known = paid("ACC-201", ACCOUNT, 100, MORNING, known_at=AS_OF)
    late = paid("ACC-202", ACCOUNT, 100, MORNING, known_at=AS_OF + timedelta(seconds=1))

    found = activities((known, late), (), AS_OF, rules)

    assert [a.received for a in found if a.account_id == ACCOUNT] == [(known,)]


@pytest.mark.parametrize(
    ("event_time", "counted"),
    [
        (AS_OF - timedelta(hours=24), True),
        (AS_OF - timedelta(hours=24, microseconds=1), False),
        (AS_OF - timedelta(microseconds=1), True),
        (AS_OF, False),
    ],
    ids=["window start", "just before it", "just before as-of", "at as-of"],
)
def test_counts_only_payments_in_the_24_hours_before_the_as_of_time(
    rules: RulesConfig, event_time: datetime, counted: bool
) -> None:
    payment = paid("ACC-201", ACCOUNT, 100, event_time, known_at=event_time)

    found = activities((payment,), (), AS_OF, rules)

    assert [bool(a.received) for a in found if a.account_id == ACCOUNT] == [counted]


def test_ignores_a_profile_not_yet_known_at_the_as_of_time(
    rules: RulesConfig,
) -> None:
    late = opened(OLD, known_at=AS_OF + timedelta(seconds=1))

    found = activities((), (late,), AS_OF, rules)

    assert [a.profile for a in found] == [None]


def test_gives_every_account_named_anywhere_an_activity_in_account_id_order(
    rules: RulesConfig,
) -> None:
    payments = (
        paid("ACC-B", "ACC-A", 100, MORNING),
        paid("ACC-E", "ACC-D", 100, MORNING - timedelta(days=5)),
    )

    found = activities(payments, (opened(OLD, "ACC-C"),), AS_OF, rules)

    assert [a.account_id for a in found] == [
        "ACC-A",
        "ACC-B",
        "ACC-C",
        "ACC-D",
        "ACC-E",
    ]


def test_counts_each_sender_once() -> None:
    received = received_from("ACC-201", "ACC-202", "ACC-201")

    senders = distinct_senders(activity(received))

    assert senders == Known(2)


def test_measures_the_share_sent_on_and_the_time_from_first_in_to_last_out() -> None:
    received = (
        paid("ACC-201", ACCOUNT, 600, MORNING),
        paid("ACC-202", ACCOUNT, 400, MORNING + timedelta(hours=1)),
    )
    sent = (
        paid(ACCOUNT, "ACC-301", 500, MORNING + timedelta(hours=2)),
        paid(ACCOUNT, "ACC-302", 400, MORNING + timedelta(hours=3)),
    )

    measured = pass_through(activity(received, sent))

    assert measured == Known(PassThrough(Fraction(9, 10), timedelta(hours=3)))


def test_an_account_that_received_nothing_has_no_ratio_and_doesnt_breach(
    rules: RulesConfig,
) -> None:
    quiet = activity(sent=(paid(ACCOUNT, "ACC-301", 500, MORNING),))

    measured = pass_through(quiet)

    assert measured == Known(PassThrough(ratio=None, span=None))
    assert breach_of(ReasonCode.PASS_THROUGH, quiet, rules) == Known(False)


def test_an_account_that_sent_nothing_has_a_ratio_of_zero_and_no_span() -> None:
    measured = pass_through(activity(received_from("ACC-201")))

    assert measured == Known(PassThrough(ratio=Fraction(0), span=None))


def test_measures_account_age_in_whole_days_at_the_as_of_time() -> None:
    profile = opened(AS_OF - timedelta(days=9, hours=23))

    age = account_age_days(activity(profile=profile))

    assert age == Known(9)


def test_account_age_is_unknown_without_a_profile() -> None:
    no_profile = Activity(ACCOUNT, AS_OF, (), (), None)

    age = account_age_days(no_profile)

    assert age == Unknown(ReasonCode.PROFILE_MISSING)


@pytest.mark.parametrize(
    ("senders", "breach"),
    [(5, True), (4, False)],
    ids=["exactly 5 senders", "4 senders"],
)
def test_fan_in_breaches_from_five_senders(
    rules: RulesConfig, senders: int, breach: bool
) -> None:
    received = received_from(*(f"ACC-2{n:02}" for n in range(senders)))

    found = breach_of(ReasonCode.FAN_IN, activity(received), rules)

    assert found == Known(breach)


@pytest.mark.parametrize(
    ("sent_cents", "last_out", "breach"),
    [
        (8000, timedelta(hours=12), True),
        (7999, timedelta(hours=1), False),
        (8000, timedelta(hours=12, seconds=1), False),
    ],
    ids=["exactly 0.80 within exactly 12 hours", "just under 0.80", "just over 12h"],
)
def test_pass_through_breaches_at_0_80_within_12_hours(
    rules: RulesConfig, sent_cents: int, last_out: timedelta, breach: bool
) -> None:
    received = (paid("ACC-201", ACCOUNT, 10000, MORNING),)
    sent = (paid(ACCOUNT, "ACC-301", sent_cents, MORNING + last_out),)

    found = breach_of(ReasonCode.PASS_THROUGH, activity(received, sent), rules)

    assert found == Known(breach)


@pytest.mark.parametrize(
    ("age", "breach"),
    [(timedelta(days=30), False), (timedelta(days=30) - timedelta(seconds=1), True)],
    ids=["exactly 30 days", "a second under 30 days"],
)
def test_new_account_breaches_under_30_days_old(
    rules: RulesConfig, age: timedelta, breach: bool
) -> None:
    profile = opened(AS_OF - age)

    found = breach_of(ReasonCode.NEW_ACCOUNT, activity(profile=profile), rules)

    assert found == Known(breach)


def test_a_missing_profile_leaves_new_account_unknown_with_its_reason(
    rules: RulesConfig,
) -> None:
    no_profile = Activity(ACCOUNT, AS_OF, (), (), None)

    found = breach_of(ReasonCode.NEW_ACCOUNT, no_profile, rules)

    assert found == Unknown(ReasonCode.PROFILE_MISSING)


def test_judges_each_value_of_a_conflict(rules: RulesConfig) -> None:
    def conflicting(_: Activity) -> SignalValue[int]:
        return Conflict((3, 7))

    signal = Signal(ReasonCode.FAN_IN, conflicting, lambda n, r: n >= 5)

    found = signal.assess(activity(), rules)

    assert found.breach == Conflict((False, True))
