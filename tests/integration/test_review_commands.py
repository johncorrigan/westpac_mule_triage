from datetime import UTC, datetime
from pathlib import Path

import pytest

from mule_triage.cli import ExitCode, main
from mule_triage.clock import FixedClock

GOLDEN = Path(__file__).resolve().parents[1] / "golden"
AS_OF = "2026-11-03T00:00:00Z"
CLOCK = FixedClock(datetime(2026, 11, 8, 3, 30, tzinfo=UTC))


@pytest.fixture
def database(tmp_path: Path) -> Path:
    database = tmp_path / "triage.db"
    payments, profiles = GOLDEN / "payments.jsonl", GOLDEN / "profiles.jsonl"
    args = ["run", str(payments), str(profiles), "--as-of", AS_OF]
    exit_code = main(
        [*args, "--db", str(database), "--out", str(tmp_path / "out")], clock=CLOCK
    )
    assert exit_code == ExitCode.SUCCESS
    return database


def dispose_args(database: Path, version: int) -> list[str]:
    return [
        "dispose",
        "ACC-101",
        "--as-of",
        AS_OF,
        "--db",
        str(database),
        "--version",
        str(version),
        "--reason",
        "FALSE_POSITIVE",
        "--reviewer",
        "REV-1",
        "--narrative",
        "DISCARDED",
    ]


def test_show_displays_one_case_with_its_version_and_history(
    database: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    capsys.readouterr()

    exit_code = main(["show", "ACC-101", "--as-of", AS_OF, "--db", str(database)])

    shown = capsys.readouterr().out
    assert exit_code == ExitCode.SUCCESS
    assert shown.splitlines()[0] == "Case status: OPEN, version 1"
    assert "5 of 9 shown; 4 left out" in shown
    assert "CASE_OPENED" in shown
    assert shown.isascii()


def test_dispose_closes_the_case_at_the_version_the_reviewer_saw(
    database: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    capsys.readouterr()

    exit_code = main(dispose_args(database, version=1), clock=CLOCK)

    assert exit_code == ExitCode.SUCCESS
    assert "closed at version 2" in capsys.readouterr().out


def test_dispose_on_an_old_version_tells_the_reviewer_to_re_read_the_case(
    database: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    main(dispose_args(database, version=1), clock=CLOCK)

    exit_code = main(dispose_args(database, version=1), clock=CLOCK)

    assert exit_code == ExitCode.STALE_VERSION
    assert "re-read the case" in capsys.readouterr().err


@pytest.mark.parametrize("command", ["run", "show", "dispose"])
def test_a_database_sqlite_cant_use_stops_with_a_message_not_a_traceback(
    tmp_path: Path, command: str, capsys: pytest.CaptureFixture[str]
) -> None:
    broken = tmp_path / "broken.db"
    broken.write_bytes(b"not a database, " * 64)
    args = {
        "run": [
            "run",
            str(GOLDEN / "payments.jsonl"),
            str(GOLDEN / "profiles.jsonl"),
            "--as-of",
            AS_OF,
            "--db",
            str(broken),
            "--out",
            str(tmp_path / "out"),
        ],
        "show": ["show", "ACC-101", "--as-of", AS_OF, "--db", str(broken)],
        "dispose": dispose_args(broken, version=1),
    }[command]

    exit_code = main(args, clock=CLOCK)

    assert exit_code == ExitCode.DATABASE_UNAVAILABLE
    assert "nothing was changed" in capsys.readouterr().err


@pytest.mark.parametrize("command", ["show", "dispose"])
def test_a_missing_database_is_a_usage_error_and_isnt_created(
    tmp_path: Path, command: str
) -> None:
    absent = tmp_path / "absent.db"
    args = dispose_args(absent, version=1)
    if command == "show":
        args = ["show", "ACC-101", "--as-of", AS_OF, "--db", str(absent)]

    exit_code = main(args, clock=CLOCK)

    assert exit_code == ExitCode.USAGE_ERROR
    assert not absent.exists()
