import pytest

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
    SignalValue,
    Unknown,
)
from mule_triage.rules import outcome_for
from mule_triage.signals import Assessment

ACCOUNT = AccountId("ACC-101")
FAN_IN = ReasonCode.FAN_IN
PASS_THROUGH = ReasonCode.PASS_THROUGH
NEW_ACCOUNT = ReasonCode.NEW_ACCOUNT
MISSING = Unknown(ReasonCode.PROFILE_MISSING)
YES: SignalValue[bool] = Known(True)
NO: SignalValue[bool] = Known(False)


def signals(
    fan_in: SignalValue[bool], pass_through: SignalValue[bool], age: SignalValue[bool]
) -> tuple[Assessment, ...]:
    return (
        Assessment(FAN_IN, fan_in),
        Assessment(PASS_THROUGH, pass_through),
        Assessment(NEW_ACCOUNT, age),
    )


@pytest.mark.parametrize(
    ("assessed", "expected"),
    [
        (
            signals(YES, YES, YES),
            Alert(
                ACCOUNT,
                frozenset({FAN_IN, PASS_THROUGH, NEW_ACCOUNT}),
                Priority.HIGH,
                incomplete=False,
            ),
        ),
        (
            signals(YES, NO, YES),
            Alert(
                ACCOUNT,
                frozenset({FAN_IN, NEW_ACCOUNT}),
                Priority.MEDIUM,
                incomplete=False,
            ),
        ),
        (
            signals(YES, YES, MISSING),
            Alert(
                ACCOUNT,
                frozenset({FAN_IN, PASS_THROUGH, ReasonCode.PROFILE_MISSING}),
                Priority.HIGH,
                incomplete=True,
            ),
        ),
        (
            signals(NO, YES, MISSING),
            NeedsReview(ACCOUNT, frozenset({PASS_THROUGH, ReasonCode.PROFILE_MISSING})),
        ),
        (
            signals(NO, YES, Conflict((False, True))),
            NeedsReview(
                ACCOUNT, frozenset({PASS_THROUGH, ReasonCode.CONFLICTING_RECORDS})
            ),
        ),
        (signals(NO, NO, YES), NoAlert(ACCOUNT, frozenset({NEW_ACCOUNT}))),
        (
            signals(NO, NO, MISSING),
            NoAlert(ACCOUNT, frozenset({ReasonCode.PROFILE_MISSING})),
        ),
        (signals(NO, NO, NO), NoAlert(ACCOUNT, frozenset())),
    ],
    ids=[
        "all three breach: HIGH",
        "two breach: MEDIUM",
        "two breach and one Unknown: HIGH and INCOMPLETE",
        "one breach and one Unknown: NEEDS_REVIEW",
        "a Conflict counts as Unknown",
        "one breach: NO_ALERT with its reason",
        "only an Unknown: NO_ALERT with its reason",
        "nothing breaches: NO_ALERT with no reasons",
    ],
)
def test_turns_an_accounts_signals_into_its_outcome(
    rules: RulesConfig, assessed: tuple[Assessment, ...], expected: AccountResult
) -> None:
    result = outcome_for(ACCOUNT, assessed, rules)

    assert result == expected
