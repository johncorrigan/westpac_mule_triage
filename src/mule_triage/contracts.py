"""The data that crosses a boundary: the inputs, the rules, the signal values and the results.

The inputs and the rules are strict, so nothing is coerced: "90000" or 900.0 isn't read as cents,
nor a number as a time, nor "12" as hours.
"""

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum
from pathlib import Path
from typing import Annotated, ClassVar, Generic, NewType, Self, TypeAlias, TypeVar
from urllib.parse import urlsplit

from pydantic import (
    AfterValidator,
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    model_validator,
)

TransactionId = NewType("TransactionId", str)
AccountId = NewType("AccountId", str)

# Account IDs are printed, so a newline or control character could forge a line of output.
# pydantic's regex engine reads `$` as the end of the text, so a trailing newline doesn't match.
ACCOUNT_ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,63}$"
PlainAccountId = Annotated[AccountId, StringConstraints(pattern=ACCOUNT_ID_PATTERN)]

# SQLite's largest integer, so every accepted amount can be stored.
MAX_AMOUNT_CENTS = 2**63 - 1


def to_utc(moment: datetime) -> datetime:
    return moment.astimezone(UTC)


UtcDatetime = Annotated[AwareDatetime, AfterValidator(to_utc)]


class Payment(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    transaction_id: TransactionId
    from_account: PlainAccountId
    to_account: PlainAccountId
    amount_cents: int = Field(gt=0, le=MAX_AMOUNT_CENTS)
    event_time: UtcDatetime
    knowledge_time: UtcDatetime
    reference: str


class AccountProfile(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    account_id: PlainAccountId
    opened_at: UtcDatetime
    knowledge_time: UtcDatetime


class FanInRule(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    min_senders: int = Field(ge=1)


class PassThroughRule(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    min_ratio: Decimal = Field(gt=0)
    max_hours: int = Field(gt=0)


class NewAccountRule(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    younger_than_days: int = Field(ge=1)


class OutcomeRule(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    alert_at: int = Field(ge=1)
    high_priority_at: int = Field(ge=1)

    @model_validator(mode="after")
    def high_priority_is_never_below_an_alert(self) -> Self:
        if self.high_priority_at < self.alert_at:
            raise ValueError("high_priority_at is below alert_at")
        return self


class EvidenceRule(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    max_counterparties: int = Field(ge=1)


class RulesConfig(BaseModel):
    """Read from TOML with floats as `Decimal`, so a ratio of 0.80 is exactly 4/5."""

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    version: str = Field(min_length=1)
    window_hours: int = Field(gt=0)
    fan_in: FanInRule
    pass_through: PassThroughRule
    new_account: NewAccountRule
    outcome: OutcomeRule
    evidence: EvidenceRule


class ReasonCode(StrEnum):
    FAN_IN = "FAN_IN"
    PASS_THROUGH = "PASS_THROUGH"
    NEW_ACCOUNT = "NEW_ACCOUNT"
    PROFILE_MISSING = "PROFILE_MISSING"
    CONFLICTING_RECORDS = "CONFLICTING_RECORDS"


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


class NarrativeOutcome(StrEnum):
    """AI_DRAFT is the model's checked draft; every other outcome is the template."""

    AI_DRAFT = "AI_DRAFT"
    AI_OFF = "AI_OFF"
    NEEDS_REVIEW = "NEEDS_REVIEW"
    NO_RECORDING = "NO_RECORDING"
    AI_UNAVAILABLE = "AI_UNAVAILABLE"
    REJECTED = "REJECTED"


@dataclass(frozen=True, slots=True)
class NarrativeSummary:
    account_id: AccountId
    outcome: NarrativeOutcome
    latency_seconds: float | None


@dataclass(frozen=True, slots=True)
class RunResult:
    payment_counts: IngestCounts
    # A rejected line's ID is only the text it held, so it isn't a TransactionId.
    rejected_payment_ids: tuple[str, ...]
    duplicate_payment_ids: tuple[TransactionId, ...]
    accounts: tuple[AccountResult, ...]
    narratives: tuple[NarrativeSummary, ...] = ()


class AiMode(StrEnum):
    OFF = "off"
    REPLAY = "replay"
    LIVE = "live"


DEFAULT_MODEL = "granite3.3:8b"
MODEL_NAME_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$"


def plain_endpoint(text: str) -> str:
    """An http or https base URL, such as http://localhost:11434, with nothing after it."""
    parts = urlsplit(text)
    plain = (
        parts.scheme in ("http", "https")
        and bool(parts.hostname)
        and parts.path in ("", "/")
        and not (parts.query or parts.fragment or parts.username or parts.password)
    )
    if not plain:
        raise ValueError(
            "must be http:// or https:// and a host, with nothing after it"
        )
    # Reading `port` raises ValueError for a port that isn't a number up to 65535.
    if parts.port == 0:
        raise ValueError("needs a port from 1 to 65535")
    return text.rstrip("/")


class AiSettings(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    mode: AiMode = AiMode.REPLAY
    model: Annotated[str, StringConstraints(pattern=MODEL_NAME_PATTERN)] = DEFAULT_MODEL
    endpoint: Annotated[str, AfterValidator(plain_endpoint)] = "http://localhost:11434"
    timeout_seconds: int = Field(default=60, ge=1, le=600)
    record: bool = False
    recordings: Path = Path("tests/ai/recorded")

    @model_validator(mode="after")
    def records_only_live_replies(self) -> Self:
        if self.record and self.mode is not AiMode.LIVE:
            raise ValueError("--record needs --ai live")
        return self
