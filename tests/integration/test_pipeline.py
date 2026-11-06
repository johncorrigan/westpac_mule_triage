import json
import re
import sqlite3
from contextlib import AbstractContextManager, closing
from dataclasses import replace
from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from mule_triage.contracts import (
    AccountProfile,
    AccountResult,
    NoAlert,
    Payment,
    RulesConfig,
)
from mule_triage.errors import ReconciliationError
from mule_triage.ingest import ParsedFile
from mule_triage.pipeline import Settings, record_run, run
from mule_triage.store import (
    CaseSnapshot,
    CaseVersion,
    RunId,
    RunTally,
    SqliteStore,
    open_store,
)

AS_OF = datetime(2026, 11, 3, tzinfo=UTC)
AS_OF_TEXT = "2026-11-03T00:00:00.000000+00:00"
GOLDEN = Path(__file__).resolve().parents[1] / "golden"
NEW_PT = '["NEW_ACCOUNT", "PASS_THROUGH"]'
PT_MISSING = '["PASS_THROUGH", "PROFILE_MISSING"]'
ACC_101 = (
    "ACC-101",
    AS_OF_TEXT,
    "ALERT",
    "HIGH",
    0,
    '["FAN_IN", "NEW_ACCOUNT", "PASS_THROUGH"]',
    1,
    1,
)
ACC_105 = ("ACC-105", AS_OF_TEXT, "NEEDS_REVIEW", None, 0, PT_MISSING, 1, 1)
ACC_106 = (
    "ACC-106",
    AS_OF_TEXT,
    "ALERT",
    "HIGH",
    1,
    '["FAN_IN", "PASS_THROUGH", "PROFILE_MISSING"]',
    1,
    1,
)
GOLDEN_CASES: list[tuple[object, ...]] = [ACC_101, ACC_105, ACC_106]
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


class FaultyStore:
    """Passes everything to the real store, except for the one fault each test gives it."""

    def __init__(
        self,
        real: SqliteStore,
        drop_first_payment: bool = False,
        fail_after_save: bool = False,
        fail_after_outcomes: bool = False,
    ) -> None:
        self.real = real
        self.drop_first_payment = drop_first_payment
        self.fail_after_save = fail_after_save
        self.fail_after_outcomes = fail_after_outcomes

    def transaction(self) -> AbstractContextManager[None]:
        return self.real.transaction()

    def save(
        self,
        as_of: datetime,
        payments: ParsedFile[Payment],
        profiles: ParsedFile[AccountProfile],
    ) -> RunId:
        if self.drop_first_payment:
            payments = replace(payments, accepted=payments.accepted[1:])
        run_id = self.real.save(as_of, payments, profiles)
        if self.fail_after_save:
            raise sqlite3.OperationalError("disk I/O error")
        return run_id

    def tally(self, run_id: RunId) -> RunTally:
        return self.real.tally(run_id)

    def stored_payments(self) -> tuple[Payment, ...]:
        return self.real.stored_payments()

    def stored_profiles(self) -> tuple[AccountProfile, ...]:
        return self.real.stored_profiles()

    def save_outcomes(
        self,
        run_id: RunId,
        rules: RulesConfig,
        snapshots: tuple[CaseSnapshot, ...],
    ) -> None:
        self.real.save_outcomes(run_id, rules, snapshots)
        if self.fail_after_outcomes:
            raise sqlite3.OperationalError("disk I/O error")

    def case_versions(self, as_of: datetime) -> tuple[CaseVersion, ...]:
        return self.real.case_versions(as_of)


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


def run_golden(
    database: Path,
    rules: RulesConfig,
    as_of: datetime = AS_OF,
    payments: Path = GOLDEN / "payments.jsonl",
) -> tuple[AccountResult, ...]:
    return run(
        payments,
        GOLDEN / "profiles.jsonl",
        as_of,
        Settings(database, rules, database.parent / "out"),
    ).accounts


def stored_cases(database: Path) -> list[tuple[object, ...]]:
    with closing(sqlite3.connect(database)) as connection:
        return connection.execute(
            "SELECT account_id, as_of, outcome, priority, incomplete, reasons, version,"
            " changed_in_run FROM cases ORDER BY as_of, account_id"
        ).fetchall()


@pytest.fixture
def stored_once(tmp_path: Path) -> Path:
    database = tmp_path / "triage.db"
    with open_store(database) as store:
        record_run(*write_inputs(tmp_path, "TX-100-01"), AS_OF, store)
    return database


@pytest.mark.parametrize(
    ("fault", "error", "message"),
    [
        ({"drop_first_payment": True}, ReconciliationError, "^payments: received 3"),
        ({"fail_after_save": True}, sqlite3.OperationalError, "disk I/O error"),
        ({"fail_after_outcomes": True}, sqlite3.OperationalError, "disk I/O error"),
    ],
    ids=["unbalanced", "fails after the save", "fails after storing outcomes"],
)
def test_a_run_that_fails_leaves_the_database_unchanged(
    tmp_path: Path,
    stored_once: Path,
    rules: RulesConfig,
    fault: dict[str, bool],
    error: type[Exception],
    message: str,
) -> None:
    inputs = write_inputs(tmp_path, "TX-200-01")
    before = dump(stored_once)
    with open_store(stored_once) as real:

        with pytest.raises(error, match=message):
            run(
                *inputs,
                AS_OF,
                Settings(stored_once, rules, tmp_path / "out"),
                FaultyStore(real, **fault),
            )

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


def test_stores_each_alert_and_review_with_a_version_and_the_rules_version(
    tmp_path: Path, rules: RulesConfig
) -> None:
    database = tmp_path / "golden.db"

    run_golden(database, rules)

    with closing(sqlite3.connect(database)) as connection:
        versions = connection.execute("SELECT rules_version FROM runs").fetchall()
    assert stored_cases(database) == GOLDEN_CASES
    assert versions == [("v1",)]


def test_a_rerun_with_the_same_results_leaves_every_case_alone(
    tmp_path: Path, rules: RulesConfig
) -> None:
    database = tmp_path / "golden.db"
    run_golden(database, rules)

    run_golden(database, rules)

    assert stored_cases(database) == GOLDEN_CASES


def test_a_changed_result_raises_the_version_and_records_the_run(
    tmp_path: Path, rules: RulesConfig
) -> None:
    database = tmp_path / "golden.db"
    fewer_fan_ins = rules.model_copy(
        update={"fan_in": rules.fan_in.model_copy(update={"min_senders": 50})}
    )
    run_golden(database, rules)

    run_golden(database, fewer_fan_ins)

    assert stored_cases(database) == [
        ("ACC-101", AS_OF_TEXT, "ALERT", "MEDIUM", 0, NEW_PT, 2, 2),
        # Its outcome holds, but its pack now shows the FAN_IN threshold as 50.
        ("ACC-105", AS_OF_TEXT, "NEEDS_REVIEW", None, 0, PT_MISSING, 2, 2),
        ("ACC-106", AS_OF_TEXT, "NEEDS_REVIEW", None, 0, PT_MISSING, 2, 2),
    ]


def test_one_more_payment_into_acc_101_raises_only_its_version(
    tmp_path: Path, rules: RulesConfig
) -> None:
    database = tmp_path / "golden.db"
    run_golden(database, rules)
    more = tmp_path / "payments.jsonl"
    extra = {
        **PAYMENT,
        "transaction_id": "TX-101-99",
        "from_account": "ACC-208",
        "event_time": "2026-11-02T11:45:00Z",
        "knowledge_time": "2026-11-02T11:46:00Z",
    }
    golden = (GOLDEN / "payments.jsonl").read_text(encoding="utf-8")
    more.write_text(golden + json.dumps(extra) + "\n", encoding="utf-8")

    run_golden(database, rules, payments=more)

    assert stored_cases(database) == [(*ACC_101[:6], 2, 2), ACC_105, ACC_106]


def test_writes_a_markdown_and_a_json_pack_for_every_case(
    tmp_path: Path, rules: RulesConfig
) -> None:
    database = tmp_path / "golden.db"

    run_golden(database, rules)

    names = sorted(path.name for path in (tmp_path / "out").iterdir())
    assert names == [
        f"{account}_20261103T000000Z.{suffix}"
        for account in ("ACC-101", "ACC-105", "ACC-106")
        for suffix in ("json", "md")
    ]


def test_a_case_that_drops_to_no_alert_keeps_its_row_with_its_new_outcome(
    tmp_path: Path, rules: RulesConfig
) -> None:
    database = tmp_path / "golden.db"
    rarer_pass_through = rules.model_copy(
        update={
            "pass_through": rules.pass_through.model_copy(
                update={"min_ratio": Decimal("0.99")}
            )
        }
    )
    run_golden(database, rules)

    run_golden(database, rarer_pass_through)

    assert stored_cases(database) == [
        (
            "ACC-101",
            AS_OF_TEXT,
            "ALERT",
            "MEDIUM",
            0,
            '["FAN_IN", "NEW_ACCOUNT"]',
            2,
            2,
        ),
        ("ACC-105", AS_OF_TEXT, "NO_ALERT", None, 0, '["PROFILE_MISSING"]', 2, 2),
        (
            "ACC-106",
            AS_OF_TEXT,
            "NEEDS_REVIEW",
            None,
            0,
            '["FAN_IN", "PROFILE_MISSING"]',
            2,
            2,
        ),
    ]


def test_a_different_as_of_time_starts_its_own_cases(
    tmp_path: Path, rules: RulesConfig
) -> None:
    database = tmp_path / "golden.db"
    run_golden(database, rules)

    run_golden(database, rules, AS_OF + timedelta(hours=1))

    later = "2026-11-03T01:00:00.000000+00:00"
    assert stored_cases(database) == GOLDEN_CASES + [
        (case[0], later, *case[2:6], 1, 2) for case in GOLDEN_CASES
    ]


def test_the_same_instant_in_another_timezone_is_the_same_case(
    tmp_path: Path, rules: RulesConfig
) -> None:
    database = tmp_path / "golden.db"
    run_golden(database, rules)

    run_golden(database, rules, AS_OF.astimezone(timezone(timedelta(hours=11))))

    assert stored_cases(database) == GOLDEN_CASES


def test_records_which_settings_a_run_used_not_only_their_version(
    tmp_path: Path, rules: RulesConfig
) -> None:
    database = tmp_path / "golden.db"
    stricter = rules.model_copy(
        update={"fan_in": rules.fan_in.model_copy(update={"min_senders": 50})}
    )
    run_golden(database, rules)

    run_golden(database, stricter)

    with closing(sqlite3.connect(database)) as connection:
        runs = connection.execute(
            "SELECT rules_version, rules_sha256 FROM runs ORDER BY run_id"
        ).fetchall()
    assert [version for version, _ in runs] == ["v1", "v1"]
    assert all(re.fullmatch("[0-9a-f]{64}", digest) for _, digest in runs)
    assert runs[0][1] != runs[1][1]


def test_a_rerun_gives_the_same_outcomes_from_the_stored_payments(
    tmp_path: Path, rules: RulesConfig
) -> None:
    database = tmp_path / "golden.db"
    first = run_golden(database, rules)

    second = run_golden(database, rules)

    assert second == first
    flagged = [a.account_id for a in second if not isinstance(a, NoAlert)]
    assert flagged == ["ACC-101", "ACC-105", "ACC-106"]
