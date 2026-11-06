import json
import sqlite3
from contextlib import AbstractContextManager, closing
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pytest

from mule_triage.contracts import AccountProfile, Payment
from mule_triage.errors import ReconciliationError
from mule_triage.ingest import ParsedFile
from mule_triage.pipeline import Settings, record_run, run
from mule_triage.store import RunId, RunTally, SqliteStore, open_store

AS_OF = datetime(2026, 11, 3, tzinfo=UTC)
PAYMENT = {
    "transaction_id": "TX-101-01",
    "from_account": "ACC-201",
    "to_account": "ACC-101",
    "amount_cents": 90000,
    "event_time": "2026-11-02T09:00:00Z",
    "knowledge_time": "2026-11-02T09:01:00Z",
    "reference": "rent",
}
PROFILE = {
    "account_id": "ACC-101",
    "opened_at": "2026-10-25T00:00:00Z",
    "knowledge_time": "2026-10-25T00:00:00Z",
}


class DroppingStore:
    """Passes everything to the real store, but silently loses the first payment."""

    def __init__(self, real: SqliteStore) -> None:
        self.real = real

    def transaction(self) -> AbstractContextManager[None]:
        return self.real.transaction()

    def save(
        self,
        as_of: datetime,
        payments: ParsedFile[Payment],
        profiles: ParsedFile[AccountProfile],
    ) -> RunId:
        dropped = replace(payments, accepted=payments.accepted[1:])
        return self.real.save(as_of, dropped, profiles)

    def tally(self, run_id: RunId) -> RunTally:
        return self.real.tally(run_id)


class FailingStore:
    """Writes the whole run through the real store, then fails before the tally."""

    def __init__(self, real: SqliteStore) -> None:
        self.real = real

    def transaction(self) -> AbstractContextManager[None]:
        return self.real.transaction()

    def save(
        self,
        as_of: datetime,
        payments: ParsedFile[Payment],
        profiles: ParsedFile[AccountProfile],
    ) -> RunId:
        self.real.save(as_of, payments, profiles)
        raise sqlite3.OperationalError("disk I/O error")

    def tally(self, run_id: RunId) -> RunTally:
        return self.real.tally(run_id)


def write_inputs(folder: Path, first_transaction: str) -> tuple[Path, Path]:
    payments = folder / "payments.jsonl"
    profiles = folder / "profiles.jsonl"
    lines = [
        {**PAYMENT, "transaction_id": first_transaction},
        {**PAYMENT, "transaction_id": "TX-101-02", "event_time": "2026-11-02T09:25:00"},
        {**PAYMENT, "transaction_id": "TX-101-03"},
    ]
    payments.write_text(
        "".join(json.dumps(line) + "\n" for line in lines), encoding="utf-8"
    )
    profiles.write_text(json.dumps(PROFILE) + "\n", encoding="utf-8")
    return payments, profiles


def dump(database: Path) -> list[str]:
    with closing(sqlite3.connect(database)) as connection:
        return list(connection.iterdump())


@pytest.fixture
def stored_once(tmp_path: Path) -> Path:
    database = tmp_path / "triage.db"
    with open_store(database) as store:
        record_run(*write_inputs(tmp_path, "TX-100-01"), AS_OF, store)
    return database


def test_an_unbalanced_run_fails_and_leaves_the_database_unchanged(
    tmp_path: Path, stored_once: Path
) -> None:
    inputs = write_inputs(tmp_path, "TX-200-01")
    before = dump(stored_once)
    with open_store(stored_once) as real:

        with pytest.raises(ReconciliationError, match="^payments: received 3 lines"):
            run(*inputs, AS_OF, Settings(stored_once), DroppingStore(real))

    assert dump(stored_once) == before


def test_a_failure_part_way_through_leaves_nothing_written(
    tmp_path: Path, stored_once: Path
) -> None:
    inputs = write_inputs(tmp_path, "TX-200-01")
    before = dump(stored_once)
    with open_store(stored_once) as real:

        with pytest.raises(sqlite3.OperationalError, match="disk I/O error"):
            run(*inputs, AS_OF, Settings(stored_once), FailingStore(real))

    assert dump(stored_once) == before


def test_a_balanced_run_is_committed(tmp_path: Path, stored_once: Path) -> None:
    inputs = write_inputs(tmp_path, "TX-200-01")
    with open_store(stored_once) as store:

        tally = record_run(*inputs, AS_OF, store)

    with closing(sqlite3.connect(stored_once)) as connection:
        stored = connection.execute(
            "SELECT transaction_id FROM payments WHERE run_id = ?", (tally.run_id,)
        ).fetchall()
    assert stored == [("TX-200-01",)]
