import json
from pathlib import Path

import pytest

from mule_triage.cli import SUMMARY_FILE, main

ROOT = Path(__file__).resolve().parents[2]
GOLDEN = ROOT / "tests" / "golden"
AS_OF = "2026-11-03T00:00:00Z"
EXTRA_PAYMENT = {
    "transaction_id": "TX-101-99",
    "from_account": "ACC-208",
    "to_account": "ACC-101",
    "amount_cents": 90000,
    "event_time": "2026-11-02T11:45:00Z",
    "knowledge_time": "2026-11-02T11:46:00Z",
    "reference": "rent",
}


def run_golden(
    database: Path, out: Path, payments: Path = GOLDEN / "payments.jsonl"
) -> int:
    """With the wall clock, as the command line runs it."""
    return main(
        [
            "run",
            str(payments),
            str(GOLDEN / "profiles.jsonl"),
            "--as-of",
            AS_OF,
            "--rules",
            str(ROOT / "rules" / "rules-v1.toml"),
            "--recordings",
            str(ROOT / "tests" / "ai" / "recorded"),
            "--db",
            str(database),
            "--out",
            str(out),
        ]
    )


def saved_summary(out: Path) -> dict[str, object]:
    summary: dict[str, object] = json.loads(
        (out / SUMMARY_FILE).read_text(encoding="utf-8")
    )
    return summary


def test_the_same_inputs_give_the_same_summary_bytes(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    run_golden(tmp_path / "first.db", tmp_path / "first")
    printed = capsys.readouterr().out

    run_golden(tmp_path / "second.db", tmp_path / "second")

    assert capsys.readouterr().out == printed
    first = (tmp_path / "first" / SUMMARY_FILE).read_bytes()
    assert (tmp_path / "second" / SUMMARY_FILE).read_bytes() == first


def test_a_rerun_of_the_same_files_creates_and_changes_no_case(tmp_path: Path) -> None:
    database = tmp_path / "triage.db"
    run_golden(database, tmp_path / "first")

    run_golden(database, tmp_path / "second")

    summary = saved_summary(tmp_path / "second")
    assert summary["run_id"] == 2
    assert summary["cases"] == {"created": 0, "changed": 0, "unchanged": 3}
    assert summary["payments"] == {
        "received": 35,
        "accepted": 0,
        "rejected": 1,
        "duplicates": 34,
    }


def test_a_reopened_case_counts_as_changed(tmp_path: Path) -> None:
    database = tmp_path / "triage.db"
    run_golden(database, tmp_path / "first")
    main(
        [
            "dispose",
            "ACC-101",
            "--as-of",
            AS_OF,
            "--db",
            str(database),
            "--version",
            "1",
            "--reason",
            "ESCALATED",
            "--reviewer",
            "R-1",
            "--narrative",
            "DISCARDED",
        ]
    )
    more = tmp_path / "payments.jsonl"
    golden = (GOLDEN / "payments.jsonl").read_text(encoding="utf-8")
    more.write_text(golden + json.dumps(EXTRA_PAYMENT) + "\n", encoding="utf-8")

    run_golden(database, tmp_path / "second", payments=more)

    summary = saved_summary(tmp_path / "second")
    assert summary["cases"] == {"created": 0, "changed": 1, "unchanged": 2}
