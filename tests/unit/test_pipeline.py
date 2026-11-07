import pytest

from mule_triage.errors import ReconciliationError
from mule_triage.records.ingest import Rejection
from mule_triage.pipeline import reconcile
from mule_triage.records.store import FileTally, RunId, RunTally

REJECTED = (Rejection(2, "TX-1", "event_time: timezone_aware"),)
BALANCED = FileTally(
    received=4, accepted=2, rejections=REJECTED, duplicate_ids=("TX-2",)
)


def test_a_balanced_run_reconciles() -> None:
    tally = RunTally(RunId(1), BALANCED, FileTally(1, 1, (), ()))

    reconcile(tally)


@pytest.mark.parametrize(
    ("tally", "file"),
    [
        (
            RunTally(RunId(1), FileTally(4, 1, REJECTED, ("TX-2",)), BALANCED),
            "payments",
        ),
        (RunTally(RunId(1), BALANCED, FileTally(2, 1, (), ())), "profiles"),
    ],
    ids=["payments short", "profiles short"],
)
def test_an_unbalanced_file_fails_reconciliation(tally: RunTally, file: str) -> None:
    with pytest.raises(ReconciliationError, match=f"^{file}: received"):
        reconcile(tally)
