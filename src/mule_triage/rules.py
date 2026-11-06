"""The rule in tests/golden/README.md: from one account's assessed signals to its outcome.

A Conflict counts as an Unknown. Each Unknown counts as breaching when deciding NEEDS_REVIEW and
when setting an alert's priority, so missing data never clears an account or lowers a priority.
"""

from collections.abc import Iterable
from typing import assert_never

from mule_triage.contracts import (
    AccountId,
    AccountResult,
    Alert,
    Conflict,
    Known,
    NeedsReview,
    NoAlert,
    Priority,
    ReasonCode,
    RulesConfig,
    Unknown,
)
from mule_triage.signals import SIGNALS, Assessment


def unreachable_settings(rules: RulesConfig) -> tuple[str, ...]:
    """Counts that no account could reach with the registered signals."""
    counts = {
        "alert_at": rules.outcome.alert_at,
        "high_priority_at": rules.outcome.high_priority_at,
    }
    return tuple(
        f"outcome.{name}: {count} is above the {len(SIGNALS)} signals"
        for name, count in counts.items()
        if count > len(SIGNALS)
    )


def outcome_for(
    account_id: AccountId, assessments: Iterable[Assessment], rules: RulesConfig
) -> AccountResult:
    breached: set[ReasonCode] = set()
    unknown_reasons: set[ReasonCode] = set()
    unknowns = 0
    for assessment in assessments:
        match assessment.breach:
            case Known(breach):
                if breach:
                    breached.add(assessment.code)
            case Unknown(reason):
                unknowns += 1
                unknown_reasons.add(reason)
            case Conflict():
                unknowns += 1
                unknown_reasons.add(ReasonCode.CONFLICTING_RECORDS)
            case _:
                assert_never(assessment.breach)
    reasons = frozenset(breached | unknown_reasons)
    at_worst = len(breached) + unknowns
    if len(breached) >= rules.outcome.alert_at:
        high = at_worst >= rules.outcome.high_priority_at
        priority = Priority.HIGH if high else Priority.MEDIUM
        return Alert(account_id, reasons, priority, incomplete=unknowns > 0)
    if at_worst >= rules.outcome.alert_at:
        return NeedsReview(account_id, reasons)
    return NoAlert(account_id, reasons)
