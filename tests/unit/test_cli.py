import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from mule_triage.cli import (
    DEFAULT_RULES,
    EXIT_MEANINGS,
    SUMMARY_FILE,
    ExitCode,
    Runner,
    ai_settings,
    build_parser,
    load_rules,
    main,
)
from mule_triage.contracts import (
    AccountId,
    AiMode,
    AiSettings,
    Alert,
    CaseCounts,
    IngestCounts,
    NarrativeOutcome,
    NarrativeSummary,
    NeedsReview,
    NoAlert,
    Priority,
    ReasonCode,
    RulesConfig,
    RunId,
    RunResult,
    TransactionId,
)
from mule_triage.errors import ReconciliationError, RulesError, StoreError
from mule_triage.pipeline import Settings

RULES_V1 = Path(__file__).resolve().parents[2] / "rules" / "rules-v1.toml"
AS_OF = "2026-11-03T00:00:00Z"


def never_runs(
    payments: Path, profiles: Path, as_of: datetime, settings: Settings
) -> RunResult:
    raise AssertionError("the run started")


def unbalanced(
    payments: Path, profiles: Path, as_of: datetime, settings: Settings
) -> RunResult:
    raise ReconciliationError("payments: received 3 lines, but stored 2")


def old_database(
    payments: Path, profiles: Path, as_of: datetime, settings: Settings
) -> RunResult:
    raise StoreError("triage.db: has schema version 0, not 1")


def run_args(tmp_path: Path, rules: Path) -> list[str]:
    return [
        "run",
        "payments.jsonl",
        "profiles.jsonl",
        "--as-of",
        AS_OF,
        "--rules",
        str(rules),
        "--db",
        str(tmp_path / "triage.db"),
        "--out",
        str(tmp_path / "out"),
    ]


def test_prints_usage_and_succeeds_when_given_no_command(
    capsys: pytest.CaptureFixture[str],
) -> None:
    exit_code = main([])

    assert exit_code == ExitCode.SUCCESS
    assert capsys.readouterr().out.startswith("usage: mule_triage")


def test_a_usage_error_exits_with_the_usage_error_code() -> None:
    with pytest.raises(SystemExit) as raised:
        main(["--no-such-option"])

    assert raised.value.code == ExitCode.USAGE_ERROR


def test_the_exit_codes_keep_their_documented_values() -> None:
    codes = {code.name: int(code) for code in ExitCode}

    assert codes == {
        "SUCCESS": 0,
        "UNEXPECTED_ERROR": 1,
        "USAGE_ERROR": 2,
        "RECONCILIATION_FAILED": 3,
        "DEGRADED": 4,
        "STALE_VERSION": 5,
        "DATABASE_UNAVAILABLE": 6,
    }


def test_loads_rules_v1_with_the_readmes_thresholds() -> None:
    rules = load_rules(RULES_V1)

    assert rules == RulesConfig.model_validate(
        {
            "version": "v1",
            "window_hours": 24,
            "fan_in": {"min_senders": 5},
            "pass_through": {"min_ratio": Decimal("0.80"), "max_hours": 12},
            "new_account": {"younger_than_days": 30},
            "outcome": {"alert_at": 2, "high_priority_at": 3},
            "evidence": {"max_counterparties": 5},
        }
    )


@pytest.mark.parametrize(
    ("change", "problem"),
    [
        (("min_senders = 5", "min_senders = 5\nmax_senders = 9"), "extra_forbidden"),
        (("min_senders = 5", 'min_senders = "5"'), "int_type"),
        (("min_ratio = 0.80", "min_ratio = 0"), "is_instance_of"),
        (("max_hours = 12", "max_hours = 0"), "greater_than"),
        (("max_counterparties = 5", "max_counterparties = 0"), "greater_than_equal"),
        (("younger_than_days = 30\n", ""), "missing"),
        (("high_priority_at = 3", "high_priority_at = 1"), "value_error"),
        (("[fan_in]", "[fan_in"), "isn't valid TOML"),
        (("high_priority_at = 3", "high_priority_at = 4"), "above the 3 signals"),
        (
            (
                "alert_at = 2\nhigh_priority_at = 3",
                "alert_at = 4\nhigh_priority_at = 4",
            ),
            "alert_at: 4 is above the 3 signals",
        ),
    ],
    ids=[
        "unknown setting",
        "text for a number",
        "a whole number for the ratio",
        "zero hours",
        "no counterparties",
        "missing setting",
        "high priority below an alert",
        "invalid TOML",
        "high priority out of reach",
        "alert out of reach",
    ],
)
def test_refuses_a_rules_file_with_a_bad_setting(
    tmp_path: Path, change: tuple[str, str], problem: str
) -> None:
    text = RULES_V1.read_text(encoding="utf-8")
    assert change[0] in text
    bad = tmp_path / "rules.toml"
    bad.write_text(text.replace(change[0], change[1]), encoding="utf-8")

    with pytest.raises(RulesError, match=problem):
        load_rules(bad)


def test_a_bad_rules_file_stops_the_run_with_the_usage_error_code(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    bad = tmp_path / "rules.toml"
    bad.write_text("version = 'v1'\n", encoding="utf-8")

    exit_code = main(run_args(tmp_path, bad), run=never_runs)

    assert exit_code == ExitCode.USAGE_ERROR
    assert "window_hours: missing" in capsys.readouterr().err


def test_a_missing_rules_file_stops_the_run_with_the_usage_error_code(
    tmp_path: Path,
) -> None:
    exit_code = main(run_args(tmp_path, tmp_path / "absent.toml"), run=never_runs)

    assert exit_code == ExitCode.USAGE_ERROR


def test_a_run_that_doesnt_reconcile_exits_with_the_reconciliation_code(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    exit_code = main(run_args(tmp_path, RULES_V1), run=unbalanced)

    assert exit_code == ExitCode.RECONCILIATION_FAILED
    assert "payments: received 3 lines" in capsys.readouterr().err


def test_a_database_with_another_schema_exits_with_the_usage_error_code(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    exit_code = main(run_args(tmp_path, RULES_V1), run=old_database)

    assert exit_code == ExitCode.USAGE_ERROR
    assert "schema version 0" in capsys.readouterr().err


@pytest.mark.parametrize(
    "as_of",
    ["2026-11-03T00:00:00", "3 November 2026"],
    ids=["no timezone", "not ISO 8601"],
)
def test_refuses_an_as_of_time_without_a_timezone(tmp_path: Path, as_of: str) -> None:
    args = run_args(tmp_path, RULES_V1)
    args[args.index(AS_OF)] = as_of

    with pytest.raises(SystemExit) as raised:
        main(args, run=never_runs)

    assert raised.value.code == ExitCode.USAGE_ERROR


FAN_IN, PASS_THROUGH = ReasonCode.FAN_IN, ReasonCode.PASS_THROUGH
NEW_ACCOUNT, PROFILE_MISSING = ReasonCode.NEW_ACCOUNT, ReasonCode.PROFILE_MISSING


def with_narratives(*narratives: NarrativeSummary) -> Runner:
    def runner(
        payments: Path, profiles: Path, as_of: datetime, settings: Settings
    ) -> RunResult:
        return RunResult(
            run_id=RunId(3),
            payment_counts=IngestCounts(35, 33, 1, 1),
            profile_counts=IngestCounts(39, 39, 0, 0),
            rejected_payment_ids=("TX-102-05",),
            duplicate_payment_ids=(TransactionId("TX-102-06"),),
            accounts=(
                Alert(
                    AccountId("ACC-101"),
                    frozenset({FAN_IN, PASS_THROUGH, NEW_ACCOUNT}),
                    Priority.HIGH,
                    incomplete=False,
                ),
                NeedsReview(
                    AccountId("ACC-105"), frozenset({PASS_THROUGH, PROFILE_MISSING})
                ),
                Alert(
                    AccountId("ACC-106"),
                    frozenset({FAN_IN, PASS_THROUGH, PROFILE_MISSING}),
                    Priority.HIGH,
                    incomplete=True,
                ),
                NoAlert(AccountId("ACC-107"), frozenset()),
            ),
            cases=CaseCounts(created=1, changed=1, unchanged=1),
            narratives=narratives,
        )

    return runner


DRAFTED_AND_REJECTED = with_narratives(
    NarrativeSummary(AccountId("ACC-101"), NarrativeOutcome.AI_DRAFT),
    NarrativeSummary(AccountId("ACC-105"), NarrativeOutcome.NEEDS_REVIEW),
    NarrativeSummary(AccountId("ACC-106"), NarrativeOutcome.REJECTED),
)


def log_records(stderr: str) -> list[dict[str, object]]:
    return [json.loads(line) for line in stderr.splitlines() if line.startswith("{")]


def test_prints_the_run_summary_without_times_or_durations(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    args = run_args(tmp_path, RULES_V1)

    main(args, run=DRAFTED_AND_REJECTED)

    assert capsys.readouterr().out.splitlines() == [
        "run 3, as of 2026-11-03T00:00:00Z: rules v1, prompt narrative-v1, AI replay",
        "payments: 35 received, 33 accepted, 1 rejected, 1 duplicates",
        "profiles: 39 received, 39 accepted, 0 rejected, 0 duplicates",
        "outcomes: ALERT: 2, NEEDS_REVIEW: 1, NO_ALERT: 1; INCOMPLETE alerts: 1",
        "ACC-101 ALERT HIGH FAN_IN,NEW_ACCOUNT,PASS_THROUGH",
        "ACC-105 NEEDS_REVIEW PASS_THROUGH,PROFILE_MISSING",
        "ACC-106 ALERT HIGH INCOMPLETE FAN_IN,PASS_THROUGH,PROFILE_MISSING",
        "cases: 1 created, 1 changed, 1 unchanged",
        "narratives: 2 drafted, 1 accepted",
        "fallen back: AI_OFF: 0, NEEDS_REVIEW: 1, NO_RECORDING: 0, AI_UNAVAILABLE: 0,"
        " REJECTED: 1",
        "AI_UNAVAILABLE: 0",
        "exit code 0 (SUCCESS): the run is stored, and every case has its pack",
    ]


def test_saves_the_same_run_summary_as_json_under_out(tmp_path: Path) -> None:
    args = run_args(tmp_path, RULES_V1)

    main(args, run=DRAFTED_AND_REJECTED)

    saved = json.loads((tmp_path / "out" / SUMMARY_FILE).read_text(encoding="utf-8"))
    assert saved == {
        "run_id": 3,
        "as_of": "2026-11-03T00:00:00Z",
        "rules_version": "v1",
        "prompt_version": "narrative-v1",
        "ai_mode": "replay",
        "payments": {"received": 35, "accepted": 33, "rejected": 1, "duplicates": 1},
        "profiles": {"received": 39, "accepted": 39, "rejected": 0, "duplicates": 0},
        "outcomes": {"ALERT": 2, "NEEDS_REVIEW": 1, "NO_ALERT": 1},
        "incomplete_alerts": 1,
        "flagged_accounts": [
            {
                "account_id": "ACC-101",
                "outcome": "ALERT",
                "priority": "HIGH",
                "incomplete": False,
                "reasons": ["FAN_IN", "NEW_ACCOUNT", "PASS_THROUGH"],
            },
            {
                "account_id": "ACC-105",
                "outcome": "NEEDS_REVIEW",
                "priority": None,
                "incomplete": False,
                "reasons": ["PASS_THROUGH", "PROFILE_MISSING"],
            },
            {
                "account_id": "ACC-106",
                "outcome": "ALERT",
                "priority": "HIGH",
                "incomplete": True,
                "reasons": ["FAN_IN", "PASS_THROUGH", "PROFILE_MISSING"],
            },
        ],
        "cases": {"created": 1, "changed": 1, "unchanged": 1},
        "narratives": {
            "drafted": 2,
            "accepted": 1,
            "fallen_back": {
                "AI_OFF": 0,
                "NEEDS_REVIEW": 1,
                "NO_RECORDING": 0,
                "AI_UNAVAILABLE": 0,
                "REJECTED": 1,
            },
            "ai_unavailable": 0,
        },
        "exit_code": 0,
        "exit_meaning": "SUCCESS: the run is stored, and every case has its pack",
    }


def test_every_exit_code_has_a_meaning() -> None:
    assert set(EXIT_MEANINGS) == set(ExitCode)


def test_a_finished_run_logs_its_exit_code_run_and_duration(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    args = run_args(tmp_path, RULES_V1)

    main(args, run=DRAFTED_AND_REJECTED)

    (record,) = log_records(capsys.readouterr().err)
    assert (record["event"], record["run_id"], record["level"]) == (
        "run_finished",
        3,
        "INFO",
    )
    assert record["exit_code"] == 0
    assert isinstance(record["seconds"], float) and record["seconds"] >= 0


def test_a_failed_run_logs_its_exit_code_with_no_run(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    main(run_args(tmp_path, RULES_V1), run=unbalanced)

    (record,) = log_records(capsys.readouterr().err)
    assert (record["event"], record["run_id"], record["level"]) == (
        "run_finished",
        None,
        "ERROR",
    )
    assert record["exit_code"] == ExitCode.RECONCILIATION_FAILED


def test_each_run_logs_each_record_once(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    args = run_args(tmp_path, RULES_V1)
    main(args, run=DRAFTED_AND_REJECTED)
    capsys.readouterr()

    main(args, run=DRAFTED_AND_REJECTED)

    assert len(log_records(capsys.readouterr().err)) == 1


@pytest.mark.parametrize(
    ("flags", "problem"),
    [
        (["--endpoint", "ftp://localhost:11434"], "endpoint"),
        (["--endpoint", "http://localhost:11434/api/chat"], "endpoint"),
        (["--endpoint", "localhost:11434"], "endpoint"),
        (["--model", "granite 3.3"], "model"),
        (["--ai-timeout", "0"], "timeout_seconds"),
        (["--ai-timeout", "601"], "timeout_seconds"),
        (["--record"], "--record needs --ai live"),
    ],
    ids=[
        "not http",
        "a path after the host",
        "no scheme",
        "a space in the model",
        "no time",
        "over ten minutes",
        "record without live",
    ],
)
def test_refuses_bad_ai_settings_before_the_run_starts(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    flags: list[str],
    problem: str,
) -> None:
    exit_code = main(run_args(tmp_path, RULES_V1) + flags, run=never_runs)

    assert exit_code == ExitCode.USAGE_ERROR
    assert problem in capsys.readouterr().err


def test_the_ai_defaults_to_replaying_granite_from_the_local_ollama() -> None:
    args = build_parser().parse_args(["run", "p.jsonl", "q.jsonl", "--as-of", AS_OF])

    settings = ai_settings(args)

    assert settings == AiSettings(
        mode=AiMode.REPLAY,
        model="granite3.3:8b",
        endpoint="http://localhost:11434",
        timeout_seconds=60,
        record=False,
        recordings=Path("tests/ai/recorded"),
    )


def test_an_unavailable_model_exits_with_the_degraded_code_and_says_so(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    runner = with_narratives(
        NarrativeSummary(AccountId("ACC-101"), NarrativeOutcome.AI_UNAVAILABLE)
    )

    exit_code = main(run_args(tmp_path, RULES_V1), run=runner)

    assert exit_code == ExitCode.DEGRADED
    output = capsys.readouterr()
    assert "AI_UNAVAILABLE: 1" in output.out.splitlines()
    assert "exit code 4 (DEGRADED): the run is stored" in output.out
    (record,) = log_records(output.err)
    assert (record["level"], record["exit_code"]) == ("WARNING", 4)


def test_a_missing_recording_is_counted_but_doesnt_degrade_the_run(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    runner = with_narratives(
        NarrativeSummary(AccountId("ACC-101"), NarrativeOutcome.NO_RECORDING)
    )

    exit_code = main(run_args(tmp_path, RULES_V1), run=runner)

    assert exit_code == ExitCode.SUCCESS
    assert "NO_RECORDING: 1" in capsys.readouterr().out


def test_the_run_command_defaults_to_rules_v1_and_writes_under_out() -> None:
    args = build_parser().parse_args(["run", "p.jsonl", "q.jsonl", "--as-of", AS_OF])

    assert args.rules == DEFAULT_RULES == Path("rules/rules-v1.toml")
    assert args.db.parts[0] == "out"
    assert args.out.parts[0] == "out"
    assert args.as_of == datetime(2026, 11, 3, tzinfo=UTC)
