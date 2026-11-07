"""The SQLite store. Each run is written in one transaction and tallied from its own rows.

Times are stored as ISO-8601 UTC text with microseconds, so they sort as text. They're converted
here rather than by sqlite3's datetime adapters, which are deprecated.
"""

import hashlib
import json
import sqlite3
from collections.abc import Iterator
from contextlib import closing, contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Literal, NewType, TypeAlias, assert_never

from mule_triage.contracts import (
    AccountId,
    AccountProfile,
    AccountResult,
    Alert,
    NeedsReview,
    NoAlert,
    Payment,
    RulesConfig,
)
from mule_triage.errors import StoreError
from mule_triage.records.ingest import ParsedFile, Rejection

RunId = NewType("RunId", int)
StoredValue: TypeAlias = str | int

BUSY_TIMEOUT_SECONDS = 5.0
CHANGED_SINCE_STORED = "changed_since_stored"
FIRST_CASE_VERSION = 1
# Raised with every schema change; SCHEMA sets it, as PRAGMA takes no placeholders.
SCHEMA_VERSION = 2
SELECT_SCHEMA_VERSION = "PRAGMA user_version"
COUNT_TABLES = "SELECT COUNT(*) FROM sqlite_master WHERE type = 'table'"

SCHEMA = """
PRAGMA user_version = 2;
CREATE TABLE IF NOT EXISTS runs (
    run_id INTEGER PRIMARY KEY,
    as_of TEXT NOT NULL,
    payments_received INTEGER NOT NULL,
    profiles_received INTEGER NOT NULL,
    rules_version TEXT,
    rules_sha256 TEXT
);
CREATE TABLE IF NOT EXISTS cases (
    account_id TEXT NOT NULL,
    as_of TEXT NOT NULL,
    outcome TEXT NOT NULL CHECK (outcome IN ('ALERT', 'NEEDS_REVIEW', 'NO_ALERT')),
    priority TEXT CHECK (priority IN ('HIGH', 'MEDIUM')),
    incomplete INTEGER NOT NULL CHECK (incomplete IN (0, 1)),
    reasons TEXT NOT NULL,
    facts_sha256 TEXT NOT NULL,
    version INTEGER NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('OPEN', 'CLOSED')),
    changed_in_run INTEGER NOT NULL REFERENCES runs (run_id),
    PRIMARY KEY (account_id, as_of),
    CHECK ((outcome = 'ALERT') = (priority IS NOT NULL)),
    CHECK (outcome = 'ALERT' OR incomplete = 0)
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
SELECT_PAYMENTS = (
    "SELECT transaction_id, from_account, to_account, amount_cents, event_time,"
    " knowledge_time, reference FROM payments ORDER BY transaction_id"
)
SELECT_PROFILES = (
    "SELECT account_id, opened_at, knowledge_time FROM profiles ORDER BY account_id"
)
SELECT_AS_OF = "SELECT as_of FROM runs WHERE run_id = ?"
SET_RULES = "UPDATE runs SET rules_version = ?, rules_sha256 = ? WHERE run_id = ?"
SELECT_CASES = (
    "SELECT account_id, outcome, priority, incomplete, reasons, facts_sha256, version,"
    " status FROM cases WHERE as_of = ?"
)
SELECT_CASE_VERSIONS = (
    "SELECT account_id, version FROM cases WHERE as_of = ? ORDER BY account_id"
)
SELECT_CASE = (
    "SELECT outcome, priority, incomplete, reasons, facts_sha256, version, status,"
    " changed_in_run FROM cases WHERE account_id = ? AND as_of = ?"
)
INSERT_CASE = (
    "INSERT INTO cases (outcome, priority, incomplete, reasons, facts_sha256,"
    " changed_in_run, account_id, as_of, version, status)"
    " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'OPEN')"
)
UPDATE_CASE = (
    "UPDATE cases SET outcome = ?, priority = ?, incomplete = ?, reasons = ?,"
    " facts_sha256 = ?, changed_in_run = ?, version = version + 1, status = 'OPEN'"
    " WHERE account_id = ? AND as_of = ?"
)
CLOSE_CASE = (
    "UPDATE cases SET status = 'CLOSED', version = version + 1"
    " WHERE account_id = ? AND as_of = ? AND version = ?"
)


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


@dataclass(frozen=True, slots=True)
class CaseSnapshot:
    """What a case's reviewer sees: its result, and the hash of its evidence pack's facts."""

    result: AccountResult
    facts_sha256: str


@dataclass(frozen=True, slots=True)
class CaseVersion:
    account_id: AccountId
    version: int


class CaseStatus(StrEnum):
    OPEN = "OPEN"
    CLOSED = "CLOSED"


class ChangeKind(StrEnum):
    OPENED = "OPENED"
    CHANGED = "CHANGED"
    REOPENED = "REOPENED"


@dataclass(frozen=True, slots=True)
class CaseChange:
    """A case a run opened or changed; a change to a closed case reopens it."""

    kind: ChangeKind
    snapshot: CaseSnapshot
    version: int


@dataclass(frozen=True, slots=True)
class StoredCase:
    account_id: AccountId
    outcome: str
    priority: str | None
    incomplete: bool
    reasons: tuple[str, ...]
    facts_sha256: str
    version: int
    status: CaseStatus
    changed_in_run: RunId


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


def settings_sha256(rules: RulesConfig) -> str:
    """Two rules files with the same settings match, whatever their comments or layout."""
    return hashlib.sha256(rules.model_dump_json().encode("utf-8")).hexdigest()


def case_values(snapshot: CaseSnapshot) -> tuple[StoredValue | None, ...]:
    """The fields whose change raises a case's version, as they're stored."""
    result = snapshot.result
    priority: str | None
    match result:
        case Alert():
            priority, incomplete = result.priority.value, result.incomplete
        case NeedsReview() | NoAlert():
            priority, incomplete = None, False
        case _:
            assert_never(result)
    return (
        result.outcome.value,
        priority,
        int(incomplete),
        json.dumps(sorted(result.reasons)),
        snapshot.facts_sha256,
    )


@contextmanager
def open_store(database: Path | Literal[":memory:"]) -> Iterator["SqliteStore"]:
    connection = sqlite3.connect(
        database, timeout=BUSY_TIMEOUT_SECONDS, isolation_level=None
    )
    with closing(connection):
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("PRAGMA foreign_keys = ON")
        version = connection.execute(SELECT_SCHEMA_VERSION).fetchone()[0]
        tables = connection.execute(COUNT_TABLES).fetchone()[0]
        if tables and version != SCHEMA_VERSION:
            raise StoreError(
                f"{database}: has schema version {version}, not {SCHEMA_VERSION};"
                " use a new --db or delete it"
            )
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

    def stored_payments(self) -> tuple[Payment, ...]:
        rows = self._connection.execute(SELECT_PAYMENTS).fetchall()
        return tuple(
            Payment(
                transaction_id=row[0],
                from_account=row[1],
                to_account=row[2],
                amount_cents=row[3],
                event_time=datetime.fromisoformat(row[4]),
                knowledge_time=datetime.fromisoformat(row[5]),
                reference=row[6],
            )
            for row in rows
        )

    def stored_profiles(self) -> tuple[AccountProfile, ...]:
        rows = self._connection.execute(SELECT_PROFILES).fetchall()
        return tuple(
            AccountProfile(
                account_id=row[0],
                opened_at=datetime.fromisoformat(row[1]),
                knowledge_time=datetime.fromisoformat(row[2]),
            )
            for row in rows
        )

    def save_outcomes(
        self,
        run_id: RunId,
        rules: RulesConfig,
        snapshots: tuple[CaseSnapshot, ...],
    ) -> tuple[CaseChange, ...]:
        """Cases are keyed by account and as-of time. A snapshot that matches its case
        leaves it alone, and only an ALERT or NEEDS_REVIEW opens one."""
        found = self._connection.execute(SELECT_AS_OF, (run_id,)).fetchone()
        if found is None:
            raise LookupError(f"run {run_id} isn't in the run ledger")
        as_of = found[0]
        self._connection.execute(
            SET_RULES, (rules.version, settings_sha256(rules), run_id)
        )
        stored = {
            row[0]: (tuple(row[1:6]), row[6], CaseStatus(row[7]))
            for row in self._connection.execute(SELECT_CASES, (as_of,))
        }
        changes: list[CaseChange] = []
        for snapshot in snapshots:
            values = case_values(snapshot)
            account_id = snapshot.result.account_id
            previous = stored.get(account_id)
            key = (run_id, account_id, as_of)
            if previous is None and not isinstance(snapshot.result, NoAlert):
                self._connection.execute(
                    INSERT_CASE, (*values, *key, FIRST_CASE_VERSION)
                )
                changes.append(
                    CaseChange(ChangeKind.OPENED, snapshot, FIRST_CASE_VERSION)
                )
            elif previous is not None and previous[0] != values:
                self._connection.execute(UPDATE_CASE, (*values, *key))
                closed = previous[2] is CaseStatus.CLOSED
                kind = ChangeKind.REOPENED if closed else ChangeKind.CHANGED
                changes.append(CaseChange(kind, snapshot, previous[1] + 1))
        return tuple(changes)

    @property
    def connection(self) -> sqlite3.Connection:
        """For the audit log, so its events share the store's transactions."""
        return self._connection

    def close_case(
        self, account_id: AccountId, as_of: datetime, seen_version: int
    ) -> bool:
        """One UPDATE that succeeds only on the version the reviewer saw."""
        cursor = self._connection.execute(
            CLOSE_CASE, (account_id, utc_text(as_of), seen_version)
        )
        return cursor.rowcount == 1

    def case(self, account_id: AccountId, as_of: datetime) -> StoredCase | None:
        row = self._connection.execute(
            SELECT_CASE, (account_id, utc_text(as_of))
        ).fetchone()
        if row is None:
            return None
        return StoredCase(
            account_id,
            outcome=row[0],
            priority=row[1],
            incomplete=bool(row[2]),
            reasons=tuple(json.loads(row[3])),
            facts_sha256=row[4],
            version=row[5],
            status=CaseStatus(row[6]),
            changed_in_run=RunId(row[7]),
        )

    def case_versions(self, as_of: datetime) -> tuple[CaseVersion, ...]:
        rows = self._connection.execute(SELECT_CASE_VERSIONS, (utc_text(as_of),))
        return tuple(CaseVersion(AccountId(row[0]), row[1]) for row in rows)

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
