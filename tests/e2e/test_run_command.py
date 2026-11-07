import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
GOLDEN = ROOT / "tests" / "golden"


def run_command(payments: Path, database: Path) -> subprocess.CompletedProcess[str]:
    command = [
        sys.executable,
        "-m",
        "mule_triage",
        "run",
        str(payments),
        str(GOLDEN / "profiles.jsonl"),
        "--as-of",
        "2026-11-03T00:00:00Z",
        "--db",
        str(database),
        "--out",
        str(database.parent / "out"),
        "--ai",
        "off",
    ]
    return subprocess.run(
        command, cwd=ROOT, capture_output=True, text=True, timeout=60, check=False
    )


def test_the_run_command_triages_the_golden_day_with_the_default_rules(
    tmp_path: Path,
) -> None:
    payments = GOLDEN / "payments.jsonl"

    done = run_command(payments, tmp_path / "triage.db")

    assert done.returncode == 0, done.stderr
    assert len(list((tmp_path / "out").glob("*.md"))) == 3
    assert done.stdout.splitlines()[1:] == [
        "ALERT: 2, NEEDS_REVIEW: 1, NO_ALERT: 38",
        "ACC-101 ALERT HIGH FAN_IN,NEW_ACCOUNT,PASS_THROUGH",
        "ACC-105 NEEDS_REVIEW PASS_THROUGH,PROFILE_MISSING",
        "ACC-106 ALERT HIGH INCOMPLETE FAN_IN,PASS_THROUGH,PROFILE_MISSING",
        "narratives: AI_DRAFT: 0, AI_OFF: 2, NEEDS_REVIEW: 1, NO_RECORDING: 0,"
        " AI_UNAVAILABLE: 0, REJECTED: 0",
    ]


def test_a_missing_input_file_stops_the_run_with_the_usage_error_code(
    tmp_path: Path,
) -> None:
    payments = tmp_path / "absent.jsonl"

    done = run_command(payments, tmp_path / "triage.db")

    assert done.returncode == 2
    assert done.stderr.startswith("mule_triage: ")
    assert "absent.jsonl" in done.stderr
    assert "Traceback" not in done.stderr
