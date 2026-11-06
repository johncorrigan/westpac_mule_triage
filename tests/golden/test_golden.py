import tomllib
from datetime import UTC, datetime
from pathlib import Path
from typing import Self

import pytest
from pydantic import BaseModel, ConfigDict, model_validator

from mule_triage.contracts import (
    AccountId,
    AccountResult,
    Alert,
    Outcome,
    Priority,
    ReasonCode,
    RunResult,
)
from mule_triage.pipeline import Settings, run

GOLDEN = Path(__file__).resolve().parent
AS_OF = datetime(2026, 11, 3, tzinfo=UTC)


class ExpectedIngest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    received: int
    accepted: int
    rejected: int
    duplicates: int
    rejected_ids: frozenset[str]
    duplicate_ids: frozenset[str]


class ExpectedAccount(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    outcome: Outcome
    reasons: frozenset[ReasonCode]
    priority: Priority | None = None
    incomplete: bool = False

    @model_validator(mode="after")
    def only_an_alert_has_a_priority_or_is_incomplete(self) -> Self:
        is_alert = self.outcome is Outcome.ALERT
        if is_alert and self.priority is None:
            raise ValueError("an ALERT needs a priority")
        if not is_alert and (self.priority is not None or self.incomplete):
            raise ValueError("only an ALERT has a priority or is incomplete")
        return self


class AnswerKey(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    default_outcome: Outcome
    ingest: ExpectedIngest
    accounts: dict[AccountId, ExpectedAccount]


def load_answer_key() -> AnswerKey:
    with (GOLDEN / "expected" / "outcomes.toml").open("rb") as handle:
        return AnswerKey.model_validate(tomllib.load(handle))


def run_golden(tmp_path: Path) -> RunResult:
    return run(
        GOLDEN / "payments.jsonl",
        GOLDEN / "profiles.jsonl",
        AS_OF,
        Settings(database=tmp_path / "golden.db"),
    )


def observed_ingest(result: RunResult) -> ExpectedIngest:
    counts = result.payment_counts
    return ExpectedIngest(
        received=counts.received,
        accepted=counts.accepted,
        rejected=counts.rejected,
        duplicates=counts.duplicates,
        rejected_ids=frozenset(result.rejected_payment_ids),
        duplicate_ids=frozenset(result.duplicate_payment_ids),
    )


def observed_account(result: AccountResult) -> ExpectedAccount:
    if isinstance(result, Alert):
        return ExpectedAccount(
            outcome=result.outcome,
            reasons=result.reasons,
            priority=result.priority,
            incomplete=result.incomplete,
        )
    return ExpectedAccount(outcome=result.outcome, reasons=result.reasons)


def results_by_account(result: RunResult) -> dict[AccountId, AccountResult]:
    by_account = {account.account_id: account for account in result.accounts}
    assert len(by_account) == len(result.accounts), "an account has two outcomes"
    return by_account


@pytest.mark.xfail(strict=True, raises=NotImplementedError)
def test_payment_ingest_matches_the_answer_key(tmp_path: Path) -> None:
    key = load_answer_key()

    result = run_golden(tmp_path)

    assert observed_ingest(result) == key.ingest


@pytest.mark.xfail(strict=True, raises=NotImplementedError)
def test_each_listed_account_matches_the_answer_key(tmp_path: Path) -> None:
    key = load_answer_key()

    result = run_golden(tmp_path)

    accounts = results_by_account(result)
    observed = {
        account: observed_account(accounts[account])
        for account in key.accounts
        if account in accounts
    }
    assert observed == key.accounts


@pytest.mark.xfail(strict=True, raises=NotImplementedError)
def test_every_other_account_gets_the_default_outcome(tmp_path: Path) -> None:
    key = load_answer_key()

    result = run_golden(tmp_path)

    others = {
        account: found.outcome
        for account, found in results_by_account(result).items()
        if account not in key.accounts
    }
    assert others, "the run reported no account beyond those in the answer key"
    assert {a: o for a, o in others.items() if o is not key.default_outcome} == {}
