"""The data that crosses a boundary: the inputs, the signal values and the results of a run.

The inputs are strict, so nothing is coerced: "90000" or 900.0 isn't read as cents, nor a number
as a time.
"""

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Annotated, ClassVar, Generic, NewType, TypeAlias, TypeVar

from pydantic import AfterValidator, AwareDatetime, BaseModel, ConfigDict, Field

TransactionId = NewType("TransactionId", str)
AccountId = NewType("AccountId", str)


def to_utc(moment: datetime) -> datetime:
    return moment.astimezone(UTC)


UtcDatetime = Annotated[AwareDatetime, AfterValidator(to_utc)]


class Payment(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    transaction_id: TransactionId
    from_account: AccountId
    to_account: AccountId
    amount_cents: int = Field(gt=0)
    event_time: UtcDatetime
    knowledge_time: UtcDatetime
    reference: str


class AccountProfile(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    account_id: AccountId
    opened_at: UtcDatetime
    knowledge_time: UtcDatetime


class ReasonCode(StrEnum):
    FAN_IN = "FAN_IN"
    PASS_THROUGH = "PASS_THROUGH"
    NEW_ACCOUNT = "NEW_ACCOUNT"
    PROFILE_MISSING = "PROFILE_MISSING"


class Outcome(StrEnum):
    ALERT = "ALERT"
    NEEDS_REVIEW = "NEEDS_REVIEW"
    NO_ALERT = "NO_ALERT"


class Priority(StrEnum):
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"


T = TypeVar("T")


@dataclass(frozen=True, slots=True)
class Known(Generic[T]):
    value: T


@dataclass(frozen=True, slots=True)
class Unknown:
    reason: ReasonCode


@dataclass(frozen=True, slots=True)
class Conflict(Generic[T]):
    values: tuple[T, ...]


SignalValue: TypeAlias = Known[T] | Unknown | Conflict[T]


@dataclass(frozen=True, slots=True)
class Alert:
    account_id: AccountId
    reasons: frozenset[ReasonCode]
    priority: Priority
    incomplete: bool
    outcome: ClassVar[Outcome] = Outcome.ALERT


@dataclass(frozen=True, slots=True)
class NeedsReview:
    account_id: AccountId
    reasons: frozenset[ReasonCode]
    outcome: ClassVar[Outcome] = Outcome.NEEDS_REVIEW


@dataclass(frozen=True, slots=True)
class NoAlert:
    account_id: AccountId
    reasons: frozenset[ReasonCode]
    outcome: ClassVar[Outcome] = Outcome.NO_ALERT


AccountResult: TypeAlias = Alert | NeedsReview | NoAlert


@dataclass(frozen=True, slots=True)
class IngestCounts:
    received: int
    accepted: int
    rejected: int
    duplicates: int


@dataclass(frozen=True, slots=True)
class RunResult:
    payment_counts: IngestCounts
    # A rejected line's ID is only the text it held, so it isn't a TransactionId.
    rejected_payment_ids: tuple[str, ...]
    duplicate_payment_ids: tuple[TransactionId, ...]
    accounts: tuple[AccountResult, ...]
