"""The SQLite store. Each run is written in one transaction and tallied from its own rows.

Times are stored as ISO-8601 UTC text with microseconds, so they sort as text. They're converted
here rather than by sqlite3's datetime adapters, which are deprecated.
"""

import sqlite3
from collections.abc import Iterator
from contextlib import closing, contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Literal, NewType, TypeAlias

from mule_triage.contracts import AccountProfile, Payment
from mule_triage.ingest import ParsedFile, Rejection

RunId = NewType("RunId", int)
StoredValue: TypeAlias = str | int

BUSY_TIMEOUT_SECONDS = 5.0
CHANGED_SINCE_STORED = "changed_since_stored"

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    run_id INTEGER PRIMARY KEY,
    as_of TEXT NOT NULL,
    payments_received INTEGER NOT NULL,
    profiles_received INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS payments (
    transaction_id TEXT PRIMARY KEY,
    from_account TEXT NOT NULL,
    to_account TEXT NOT NULL,
    amount_cents INTEGER NOT NULL,
    event_time TEXT NOT NULL,
    knowledge_time TEXT NOT NULL,
    reference TEXT NOT NULL,
    run_id INTEGER NOT NULL REFERENCES runs (run_id),
    line_number INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS profiles (
    account_id TEXT PRIMARY KEY,
    opened_at TEXT NOT NULL,
    knowledge_time TEXT NOT NULL,
    run_id INTEGER NOT NULL REFERENCES runs (run_id),
    line_number INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS rejections (
    run_id INTEGER NOT NULL REFERENCES runs (run_id),
    file TEXT NOT NULL CHECK (file IN ('payments', 'profiles')),
    line_number INTEGER NOT NULL,
    record_id TEXT,
    reason TEXT NOT NULL,
    PRIMARY KEY (run_id, file, line_number)
);
CREATE TABLE IF NOT EXISTS duplicates (
    run_id INTEGER NOT NULL REFERENCES runs (run_id),
    file TEXT NOT NULL CHECK (file IN ('payments', 'profiles')),
    line_number INTEGER NOT NULL,
    record_id TEXT NOT NULL,
    PRIMARY KEY (run_id, file, line_number)
);
"""
INSERT_RUN = (
    "INSERT INTO runs (as_of, payments_received, profiles_received) VALUES (?, ?, ?)"
)
SELECT_RECEIVED = (
    "SELECT payments_received, profiles_received FROM runs WHERE run_id = ?"
)
INSERT_REJECTION = (
    "INSERT INTO rejections (run_id, file, line_number, record_id, reason)"
    " VALUES (?, ?, ?, ?, ?)"
)
SELECT_REJECTIONS = (
    "SELECT line_number, record_id, reason FROM rejections"
    " WHERE run_id = ? AND file = ? ORDER BY line_number"
)
INSERT_DUPLICATE = (
    "INSERT INTO duplicates (run_id, file, line_number, record_id) VALUES (?, ?, ?, ?)"
)
SELECT_DUPLICATES = "SELECT record_id FROM duplicates WHERE run_id = ? AND file = ? ORDER BY line_number"


class InputFile(StrEnum):
    PAYMENTS = "payments"
    PROFILES = "profiles"


@dataclass(frozen=True, slots=True)
class RecordTable:
    file: InputFile
    id_field: str
    find: str
    insert: str
    count: str


PAYMENTS = RecordTable(
    InputFile.PAYMENTS,
    "transaction_id",
    find=(
        "SELECT from_account, to_account, amount_cents, event_time, knowledge_time,"
        " reference FROM payments WHERE transaction_id = ?"
    ),
    insert=(
        "INSERT INTO payments (transaction_id, from_account, to_account, amount_cents,"
        " event_time, knowledge_time, reference, run_id, line_number)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)"
    ),
    count="SELECT COUNT(*) FROM payments WHERE run_id = ?",
)
PROFILES = RecordTable(
    InputFile.PROFILES,
    "account_id",
    find="SELECT opened_at, knowledge_time FROM profiles WHERE account_id = ?",
    insert=(
        "INSERT INTO profiles (account_id, opened_at, knowledge_time, run_id,"
        " line_number) VALUES (?, ?, ?, ?, ?)"
    ),
    count="SELECT COUNT(*) FROM profiles WHERE run_id = ?",
)


@dataclass(frozen=True, slots=True)
class FileTally:
    received: int
    accepted: int
    rejections: tuple[Rejection, ...]
    duplicate_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class RunTally:
    run_id: RunId
    payments: FileTally
    profiles: FileTally


def utc_text(moment: datetime) -> str:
    if moment.tzinfo is None:
        raise ValueError("only a timezone-aware time can be stored")
    return moment.astimezone(UTC).isoformat(timespec="microseconds")


def payment_values(payment: Payment) -> tuple[StoredValue, ...]:
    return (
        payment.from_account,
        payment.to_account,
        payment.amount_cents,
        utc_text(payment.event_time),
        utc_text(payment.knowledge_time),
        payment.reference,
    )


def profile_values(profile: AccountProfile) -> tuple[StoredValue, ...]:
    return (utc_text(profile.opened_at), utc_text(profile.knowledge_time))


@contextmanager
def open_store(database: Path | Literal[":memory:"]) -> Iterator["SqliteStore"]:
    connection = sqlite3.connect(
        database, timeout=BUSY_TIMEOUT_SECONDS, isolation_level=None
    )
    with closing(connection):
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("PRAGMA foreign_keys = ON")
        connection.executescript(SCHEMA)
        yield SqliteStore(connection)


class SqliteStore:
    """Runs in autocommit mode, so a transaction is only what `transaction()` opens."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    @contextmanager
    def transaction(self) -> Iterator[None]:
        self._connection.execute("BEGIN IMMEDIATE")
        committed = False
        try:
            yield
            self._connection.execute("COMMIT")
            committed = True
        finally:
            if not committed:
                self._connection.execute("ROLLBACK")

    def save(
        self,
        as_of: datetime,
        payments: ParsedFile[Payment],
        profiles: ParsedFile[AccountProfile],
    ) -> RunId:
        cursor = self._connection.execute(
            INSERT_RUN, (utc_text(as_of), payments.received, profiles.received)
        )
        assert cursor.lastrowid is not None, "an INSERT always sets lastrowid"
        run_id = RunId(cursor.lastrowid)
        for payment in payments.accepted:
            self._admit(
                run_id,
                PAYMENTS,
                payment.line_number,
                payment.record.transaction_id,
                payment_values(payment.record),
            )
        for profile in profiles.accepted:
            self._admit(
                run_id,
                PROFILES,
                profile.line_number,
                profile.record.account_id,
                profile_values(profile.record),
            )
        self._reject(run_id, InputFile.PAYMENTS, payments.rejections)
        self._reject(run_id, InputFile.PROFILES, profiles.rejections)
        return run_id

    def tally(self, run_id: RunId) -> RunTally:
        received = self._connection.execute(SELECT_RECEIVED, (run_id,)).fetchone()
        if received is None:
            raise LookupError(f"run {run_id} isn't in the run ledger")
        return RunTally(
            run_id,
            self._file_tally(run_id, PAYMENTS, received[0]),
            self._file_tally(run_id, PROFILES, received[1]),
        )

    def _admit(
        self,
        run_id: RunId,
        table: RecordTable,
        line_number: int,
        record_id: str,
        values: tuple[StoredValue, ...],
    ) -> None:
        stored = self._connection.execute(table.find, (record_id,)).fetchone()
        if stored is None:
            self._connection.execute(
                table.insert, (record_id, *values, run_id, line_number)
            )
        elif tuple(stored) == values:
            self._connection.execute(
                INSERT_DUPLICATE, (run_id, table.file, line_number, record_id)
            )
        else:
            reason = f"{table.id_field}: {CHANGED_SINCE_STORED}"
            self._reject(
                run_id, table.file, (Rejection(line_number, record_id, reason),)
            )

    def _reject(
        self, run_id: RunId, file: InputFile, rejections: tuple[Rejection, ...]
    ) -> None:
        self._connection.executemany(
            INSERT_REJECTION,
            [
                (
                    run_id,
                    file,
                    rejection.line_number,
                    rejection.record_id,
                    rejection.reason,
                )
                for rejection in rejections
            ],
        )

    def _file_tally(
        self, run_id: RunId, table: RecordTable, received: int
    ) -> FileTally:
        accepted = self._connection.execute(table.count, (run_id,)).fetchone()[0]
        rejections = self._connection.execute(
            SELECT_REJECTIONS, (run_id, table.file)
        ).fetchall()
        duplicates = self._connection.execute(
            SELECT_DUPLICATES, (run_id, table.file)
        ).fetchall()
        return FileTally(
            received,
            accepted,
            tuple(Rejection(*row) for row in rejections),
            tuple(row[0] for row in duplicates),
        )
