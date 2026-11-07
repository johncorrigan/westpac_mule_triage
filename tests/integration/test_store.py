import json
import sqlite3
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path

import pytest

from mule_triage.errors import StoreError
from mule_triage.records.ingest import parse_payments, parse_profiles
from mule_triage.records.store import SCHEMA_VERSION, open_store

PAYMENT = {
    "transaction_id": "TX-101-02",
    "from_account": "ACC-202",
    "to_account": "ACC-101",
    "amount_cents": 90000,
    "event_time": "2026-11-02T20:25:00+11:00",
    "knowledge_time": "2026-11-02T09:26:00Z",
    "reference": "rent",
}


def test_opens_the_database_in_wal_mode(tmp_path: Path) -> None:
    database = tmp_path / "triage.db"

    with open_store(database):
        pass

    with closing(sqlite3.connect(database)) as connection:
        mode = connection.execute("PRAGMA journal_mode").fetchone()[0]
    assert mode == "wal"


def test_marks_a_new_database_with_the_schema_version(tmp_path: Path) -> None:
    database = tmp_path / "triage.db"

    with open_store(database):
        pass

    with closing(sqlite3.connect(database)) as connection:
        version = connection.execute("PRAGMA user_version").fetchone()[0]
    assert version == SCHEMA_VERSION


def test_refuses_a_database_made_before_the_schema_had_a_version(
    tmp_path: Path,
) -> None:
    database = tmp_path / "triage.db"
    with closing(sqlite3.connect(database)) as connection:
        connection.execute("CREATE TABLE cases (account_id TEXT)")

    with pytest.raises(StoreError, match="schema version 0"):
        with open_store(database):
            pass


def test_stores_times_as_iso_8601_utc_text(tmp_path: Path) -> None:
    database = tmp_path / "triage.db"
    payments = parse_payments([json.dumps(PAYMENT)])

    with open_store(database) as store:
        with store.transaction():
            store.save(datetime(2026, 11, 3, tzinfo=UTC), payments, parse_profiles([]))

    with closing(sqlite3.connect(database)) as connection:
        row = connection.execute(
            "SELECT event_time, typeof(event_time) FROM payments"
        ).fetchone()
    assert row == ("2026-11-02T09:25:00.000000+00:00", "text")


def test_a_transaction_that_raises_stores_nothing(tmp_path: Path) -> None:
    database = tmp_path / "triage.db"
    payments = parse_payments([json.dumps(PAYMENT)])

    with open_store(database) as store:

        with pytest.raises(LookupError, match="stop"):
            with store.transaction():
                store.save(
                    datetime(2026, 11, 3, tzinfo=UTC), payments, parse_profiles([])
                )
                raise LookupError("stop")

    with closing(sqlite3.connect(database)) as connection:
        counts = connection.execute(
            "SELECT (SELECT COUNT(*) FROM runs), (SELECT COUNT(*) FROM payments)"
        ).fetchone()
    assert counts == (0, 0)
