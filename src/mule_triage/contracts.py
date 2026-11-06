"""The input contracts: one payment or one account profile per JSON line.

Strict, so nothing is coerced: "90000" or 900.0 isn't read as cents, nor a number as a time.
"""

from datetime import UTC, datetime
from typing import Annotated, NewType

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
