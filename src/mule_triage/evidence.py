"""The evidence pack: one case's outcome and the facts behind it, from its stored records.

Fact IDs come from their records, and the facts' JSON is canonical, so the same data always
gives the same bytes and the same hash. Shown numbers are rounded away from a breach: a ratio
down and hours up, so a value just short of a threshold never looks as if it reached it.
"""

import hashlib
import json
import math
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from enum import StrEnum
from fractions import Fraction
from typing import TypeAlias, TypeVar, assert_never

from mule_triage.contracts import (
    AccountId,
    AccountResult,
    Alert,
    Conflict,
    Known,
    NeedsReview,
    NoAlert,
    Payment,
    ReasonCode,
    RulesConfig,
    SignalValue,
    Unknown,
)
from mule_triage.rules import outcome_for
from mule_triage.signals import (
    Activity,
    PassThrough,
    account_age_days,
    assess,
    distinct_senders,
    pass_through,
)

T = TypeVar("T")
JsonObject: TypeAlias = dict[str, object]

PAYMENTS_SOURCE = "payments"
PROFILES_SOURCE = "profiles"
MICROSECONDS_PER_HOUR = 3_600_000_000


class SignalStatus(StrEnum):
    BREACHED = "BREACHED"
    NOT_BREACHED = "NOT_BREACHED"
    UNKNOWN = "UNKNOWN"
    CONFLICT = "CONFLICT"


@dataclass(frozen=True, slots=True)
class Measure:
    """The facts hash covers `exact` and `limit`, never the wording, so rewording a measure
    changes no case's version."""

    name: str
    value: str
    threshold: str
    exact: str
    limit: str


@dataclass(frozen=True, slots=True)
class SignalFact:
    fact_id: str
    source: str
    record_id: str
    event_time: datetime
    knowledge_time: datetime
    signal: ReasonCode
    status: SignalStatus
    measures: tuple[Measure, ...]


@dataclass(frozen=True, slots=True)
class PaymentFact:
    fact_id: str
    source: str
    record_id: str
    event_time: datetime
    knowledge_time: datetime
    from_account: AccountId
    to_account: AccountId
    amount_cents: int
    reference: str


@dataclass(frozen=True, slots=True)
class ProfileFact:
    """Its event time is when the account was opened."""

    fact_id: str
    source: str
    record_id: str
    event_time: datetime
    knowledge_time: datetime


Fact: TypeAlias = SignalFact | PaymentFact | ProfileFact


@dataclass(frozen=True, slots=True)
class MissingData:
    signal: ReasonCode
    reason: ReasonCode
    what: str
    why_it_matters: str


@dataclass(frozen=True, slots=True)
class Counterparty:
    account_id: AccountId
    total_cents: int
    fact_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class Counterparties:
    shown: tuple[Counterparty, ...]
    total: int

    @property
    def note(self) -> str:
        left_out = self.total - len(self.shown)
        return f"{len(self.shown)} of {self.total} shown; {left_out} left out"


@dataclass(frozen=True, slots=True)
class EvidencePack:
    account_id: AccountId
    as_of: datetime
    rules_version: str
    result: AccountResult
    missing: tuple[MissingData, ...]
    signals: tuple[SignalFact, ...]
    payments: tuple[PaymentFact, ...]
    profile: ProfileFact | None
    counterparties: Counterparties

    @property
    def facts(self) -> tuple[Fact, ...]:
        profile = () if self.profile is None else (self.profile,)
        return (*self.signals, *self.payments, *profile)


def build_pack(activity: Activity, rules: RulesConfig) -> EvidencePack:
    assessments = assess(activity, rules)
    result = outcome_for(activity.account_id, assessments, rules)
    payments = payment_facts(activity)
    return EvidencePack(
        account_id=activity.account_id,
        as_of=activity.as_of,
        rules_version=rules.version,
        result=result,
        missing=tuple(
            missing_data(assessment.code, assessment.breach, result)
            for assessment in assessments
            if not isinstance(assessment.breach, Known)
        ),
        signals=tuple(
            SignalFact(
                fact_id=f"S-{assessment.code}",
                source=f"rules {rules.version}",
                record_id=assessment.code,
                event_time=activity.as_of,
                knowledge_time=activity.as_of,
                signal=assessment.code,
                status=status(assessment.breach),
                measures=MEASURES[assessment.code](activity, rules),
            )
            for assessment in assessments
        ),
        payments=payments,
        profile=(
            None
            if activity.profile is None
            else ProfileFact(
                fact_id=f"PR-{activity.profile.account_id}",
                source=PROFILES_SOURCE,
                record_id=activity.profile.account_id,
                event_time=activity.profile.opened_at,
                knowledge_time=activity.profile.knowledge_time,
            )
        ),
        counterparties=counterparties(
            activity.account_id, payments, rules.evidence.max_counterparties
        ),
    )


def payment_facts(activity: Activity) -> tuple[PaymentFact, ...]:
    """In time order, and once each, even for a payment the account made to itself."""
    unique = {p.transaction_id: p for p in (*activity.received, *activity.sent)}
    ordered = sorted(unique.values(), key=lambda p: (p.event_time, p.transaction_id))
    return tuple(payment_fact(payment) for payment in ordered)


def payment_fact(payment: Payment) -> PaymentFact:
    return PaymentFact(
        fact_id=f"P-{payment.transaction_id}",
        source=PAYMENTS_SOURCE,
        record_id=payment.transaction_id,
        event_time=payment.event_time,
        knowledge_time=payment.knowledge_time,
        from_account=payment.from_account,
        to_account=payment.to_account,
        amount_cents=payment.amount_cents,
        reference=payment.reference,
    )


def counterparties(
    account: AccountId, payments: tuple[PaymentFact, ...], most: int
) -> Counterparties:
    totals: dict[AccountId, int] = {}
    cited: dict[AccountId, list[str]] = {}
    for payment in payments:
        received = payment.to_account == account
        other = payment.from_account if received else payment.to_account
        if other == account:
            continue
        totals[other] = totals.get(other, 0) + payment.amount_cents
        cited.setdefault(other, []).append(payment.fact_id)
    ranked = sorted(totals, key=lambda other: (-totals[other], other))
    return Counterparties(
        shown=tuple(
            Counterparty(other, totals[other], tuple(cited[other]))
            for other in ranked[:most]
        ),
        total=len(ranked),
    )


def status(breach: SignalValue[bool]) -> SignalStatus:
    match breach:
        case Known(breached):
            return SignalStatus.BREACHED if breached else SignalStatus.NOT_BREACHED
        case Unknown():
            return SignalStatus.UNKNOWN
        case Conflict():
            return SignalStatus.CONFLICT
        case _:
            assert_never(breach)


WHAT_IS_MISSING = {
    ReasonCode.PROFILE_MISSING: (
        "No account profile is known by the as-of time, so the account's age"
        " can't be measured."
    ),
    ReasonCode.CONFLICTING_RECORDS: (
        "The stored records disagree, so this signal can't be measured."
    ),
}


def missing_data(
    signal: ReasonCode, breach: SignalValue[bool], result: AccountResult
) -> MissingData:
    reason = (
        breach.reason if isinstance(breach, Unknown) else ReasonCode.CONFLICTING_RECORDS
    )
    match result:
        case NeedsReview():
            why = (
                "Counted as breaching, it would make this account an ALERT, so a"
                " person decides rather than the tool clearing it."
            )
        case Alert():
            why = (
                "It counts as breaching when setting the priority, so the priority"
                " may be higher than the known signals alone would give."
            )
        case NoAlert():
            why = "Even counted as breaching, it wouldn't make this account an ALERT."
        case _:
            assert_never(result)
    what = WHAT_IS_MISSING.get(reason, "This signal can't be measured.")
    return MissingData(signal, reason, what, why)


def shown(value: SignalValue[T], show: Callable[[T], str]) -> str:
    match value:
        case Known(measured):
            return show(measured)
        case Unknown(reason):
            return f"unknown ({reason})"
        case Conflict(values):
            return "conflicting: " + ", ".join(show(each) for each in values)
        case _:
            assert_never(value)


def exact(value: SignalValue[T], show: Callable[[T], str]) -> str:
    match value:
        case Known(measured):
            return show(measured)
        case Unknown(reason):
            return f"UNKNOWN:{reason}"
        case Conflict(values):
            return "CONFLICT:" + ",".join(show(each) for each in values)
        case _:
            assert_never(value)


def exact_ratio(ratio: Fraction | None) -> str:
    return "none" if ratio is None else f"{ratio.numerator}/{ratio.denominator}"


def exact_microseconds(span: timedelta | None) -> str:
    return "none" if span is None else str(span // timedelta(microseconds=1))


def exact_decimal(number: Decimal) -> str:
    return f"{number.normalize():f}"


def hundredths(count: int) -> str:
    return f"{Decimal(count).scaleb(-2):f}"


def dollars(cents: int) -> str:
    return hundredths(cents)


def ratio_down(ratio: Fraction | None) -> str:
    return "nothing came in" if ratio is None else hundredths(math.floor(ratio * 100))


def hours_up(span: timedelta | None) -> str:
    if span is None:
        return "nothing came in or went out"
    hours = Fraction(span // timedelta(microseconds=1), MICROSECONDS_PER_HOUR)
    return hundredths(math.ceil(hours * 100))


def fan_in_measures(activity: Activity, rules: RulesConfig) -> tuple[Measure, ...]:
    senders = distinct_senders(activity)
    least = rules.fan_in.min_senders
    return (
        Measure(
            "distinct senders",
            shown(senders, str),
            f"at least {least}",
            exact(senders, str),
            str(least),
        ),
    )


def pass_through_measures(
    activity: Activity, rules: RulesConfig
) -> tuple[Measure, ...]:
    measured = pass_through(activity)
    limits = rules.pass_through

    def ratio(value: PassThrough) -> str:
        return ratio_down(value.ratio)

    def span(value: PassThrough) -> str:
        return hours_up(value.span)

    def ratio_exact(value: PassThrough) -> str:
        return exact_ratio(value.ratio)

    def span_exact(value: PassThrough) -> str:
        return exact_microseconds(value.span)

    return (
        Measure(
            "share sent on",
            shown(measured, ratio),
            f"at least {limits.min_ratio}",
            exact(measured, ratio_exact),
            exact_decimal(limits.min_ratio),
        ),
        Measure(
            "hours from first payment in to last payment out",
            shown(measured, span),
            f"from 0 to {limits.max_hours}",
            exact(measured, span_exact),
            str(limits.max_hours),
        ),
    )


def new_account_measures(activity: Activity, rules: RulesConfig) -> tuple[Measure, ...]:
    age = account_age_days(activity)
    younger_than = rules.new_account.younger_than_days
    return (
        Measure(
            "account age in days",
            shown(age, str),
            f"fewer than {younger_than}",
            exact(age, str),
            str(younger_than),
        ),
    )


MEASURES: dict[ReasonCode, Callable[[Activity, RulesConfig], tuple[Measure, ...]]] = {
    ReasonCode.FAN_IN: fan_in_measures,
    ReasonCode.PASS_THROUGH: pass_through_measures,
    ReasonCode.NEW_ACCOUNT: new_account_measures,
}


def utc_iso(moment: datetime) -> str:
    return moment.astimezone(UTC).isoformat().replace("+00:00", "Z")


def fact_document(fact: Fact) -> JsonObject:
    common: JsonObject = {
        "fact_id": fact.fact_id,
        "source": fact.source,
        "record_id": fact.record_id,
        "event_time": utc_iso(fact.event_time),
        "knowledge_time": utc_iso(fact.knowledge_time),
    }
    match fact:
        case SignalFact():
            return {
                **common,
                "signal": fact.signal.value,
                "status": fact.status.value,
                "measures": [
                    {"name": m.name, "value": m.value, "threshold": m.threshold}
                    for m in fact.measures
                ],
            }
        case PaymentFact():
            return {
                **common,
                "from_account": fact.from_account,
                "to_account": fact.to_account,
                "amount_dollars": dollars(fact.amount_cents),
                "reference": fact.reference,
            }
        case ProfileFact():
            return common
        case _:
            assert_never(fact)


def fact_record(fact: Fact) -> JsonObject:
    """The fact as the hash sees it: exact values and codes, with no wording or rounding."""
    common: JsonObject = {
        "fact_id": fact.fact_id,
        "source": fact.source,
        "record_id": fact.record_id,
        "event_time": utc_iso(fact.event_time),
        "knowledge_time": utc_iso(fact.knowledge_time),
    }
    match fact:
        case SignalFact():
            return {
                **common,
                "signal": fact.signal.value,
                "status": fact.status.value,
                "measures": [
                    {"exact": m.exact, "limit": m.limit} for m in fact.measures
                ],
            }
        case PaymentFact():
            return {
                **common,
                "from_account": fact.from_account,
                "to_account": fact.to_account,
                "amount_cents": fact.amount_cents,
                "reference": fact.reference,
            }
        case ProfileFact():
            return common
        case _:
            assert_never(fact)


def facts_json(pack: EvidencePack) -> str:
    """Canonical: sorted keys, no spaces and ASCII only, so equal facts are equal bytes."""
    return json.dumps(
        [fact_record(fact) for fact in pack.facts],
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    )


def facts_sha256(pack: EvidencePack) -> str:
    return hashlib.sha256(facts_json(pack).encode("ascii")).hexdigest()
