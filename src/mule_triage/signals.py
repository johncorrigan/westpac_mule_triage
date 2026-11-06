"""The signals: pure functions of one account's activity, each Known, Unknown or Conflict.

Only payments known by the as-of time, with an event time in the window before it, count, and
only a profile known by then. A new signal is a measure, a breach test and an entry in `SIGNALS`.
"""

from collections import defaultdict
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta
from fractions import Fraction
from typing import Generic, Protocol, TypeVar, assert_never

from mule_triage.contracts import (
    AccountId,
    AccountProfile,
    Conflict,
    Known,
    Payment,
    ReasonCode,
    RulesConfig,
    SignalValue,
    Unknown,
)

T = TypeVar("T")


@dataclass(frozen=True, slots=True)
class Activity:
    account_id: AccountId
    as_of: datetime
    received: tuple[Payment, ...]
    sent: tuple[Payment, ...]
    profile: AccountProfile | None


@dataclass(frozen=True, slots=True)
class PassThrough:
    """`ratio` is None when nothing came in; `span` when nothing came in or went out."""

    ratio: Fraction | None
    span: timedelta | None


@dataclass(frozen=True, slots=True)
class Assessment:
    code: ReasonCode
    breach: SignalValue[bool]


def activities(
    payments: Iterable[Payment],
    profiles: Iterable[AccountProfile],
    as_of: datetime,
    rules: RulesConfig,
) -> tuple[Activity, ...]:
    """One for every account named in a payment or profile, in account ID order."""
    window_start = as_of - timedelta(hours=rules.window_hours)
    accounts: set[AccountId] = set()
    received: defaultdict[AccountId, list[Payment]] = defaultdict(list)
    sent: defaultdict[AccountId, list[Payment]] = defaultdict(list)
    for payment in payments:
        accounts.update((payment.from_account, payment.to_account))
        if (
            payment.knowledge_time <= as_of
            and window_start <= payment.event_time < as_of
        ):
            received[payment.to_account].append(payment)
            sent[payment.from_account].append(payment)
    known_profiles: dict[AccountId, AccountProfile] = {}
    for profile in profiles:
        accounts.add(profile.account_id)
        if profile.knowledge_time <= as_of:
            known_profiles[profile.account_id] = profile
    return tuple(
        Activity(
            account,
            as_of,
            tuple(received.get(account, ())),
            tuple(sent.get(account, ())),
            known_profiles.get(account),
        )
        for account in sorted(accounts)
    )


def distinct_senders(activity: Activity) -> SignalValue[int]:
    return Known(len({payment.from_account for payment in activity.received}))


def pass_through(activity: Activity) -> SignalValue[PassThrough]:
    received = sum(payment.amount_cents for payment in activity.received)
    if received == 0:
        return Known(PassThrough(ratio=None, span=None))
    ratio = Fraction(sum(payment.amount_cents for payment in activity.sent), received)
    if not activity.sent:
        return Known(PassThrough(ratio, span=None))
    first_in = min(payment.event_time for payment in activity.received)
    last_out = max(payment.event_time for payment in activity.sent)
    return Known(PassThrough(ratio, last_out - first_in))


def account_age_days(activity: Activity) -> SignalValue[int]:
    if activity.profile is None:
        return Unknown(ReasonCode.PROFILE_MISSING)
    return Known((activity.as_of - activity.profile.opened_at).days)


def fan_in_breaches(senders: int, rules: RulesConfig) -> bool:
    return senders >= rules.fan_in.min_senders


def pass_through_breaches(measured: PassThrough, rules: RulesConfig) -> bool:
    if measured.ratio is None or measured.span is None:
        return False
    limits = rules.pass_through
    return measured.ratio >= Fraction(limits.min_ratio) and measured.span <= timedelta(
        hours=limits.max_hours
    )


def new_account_breaches(age_days: int, rules: RulesConfig) -> bool:
    return age_days < rules.new_account.younger_than_days


@dataclass(frozen=True, slots=True)
class Signal(Generic[T]):
    code: ReasonCode
    measure: Callable[[Activity], SignalValue[T]]
    breaches: Callable[[T, RulesConfig], bool]

    def assess(self, activity: Activity, rules: RulesConfig) -> Assessment:
        value = self.measure(activity)
        match value:
            case Known(measured):
                return Assessment(self.code, Known(self.breaches(measured, rules)))
            case Unknown():
                return Assessment(self.code, value)
            case Conflict(values):
                judged = tuple(self.breaches(each, rules) for each in values)
                return Assessment(self.code, Conflict(judged))
            case _:
                assert_never(value)


class Assessor(Protocol):
    """Lets `SIGNALS` hold signals whose measured values have different types."""

    def assess(self, activity: Activity, rules: RulesConfig) -> Assessment: ...


SIGNALS: tuple[Assessor, ...] = (
    Signal(ReasonCode.FAN_IN, distinct_senders, fan_in_breaches),
    Signal(ReasonCode.PASS_THROUGH, pass_through, pass_through_breaches),
    Signal(ReasonCode.NEW_ACCOUNT, account_age_days, new_account_breaches),
)


def assess(activity: Activity, rules: RulesConfig) -> tuple[Assessment, ...]:
    return tuple(signal.assess(activity, rules) for signal in SIGNALS)
