import json
import logging
import socket
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import pytest

from mule_triage.decision.audit import AuditLog, EventType
from mule_triage.cli import ExitCode, main
from mule_triage.clock import FixedClock
from mule_triage.contracts import (
    DEFAULT_MODEL,
    AccountId,
    NarrativeOutcome,
    RulesConfig,
)
from mule_triage.evidence import EvidencePack, facts_sha256
from mule_triage.logs import FIELDS
from mule_triage.narrative.client import (
    Drafted,
    DraftResult,
    DraftSource,
    ModelLabel,
    Recording,
    ReplayClient,
    write_recording,
)
from mule_triage.narrative.prompt import PROMPT_VERSION, Prompt
from mule_triage.narrative.template import NO_AI_DRAFT, template_sentences
from mule_triage.pipeline import Settings, run
from mule_triage.records.store import open_store

GOLDEN = Path(__file__).resolve().parents[1] / "golden"
AS_OF = datetime(2026, 11, 3, tzinfo=UTC)
CLOCK = FixedClock(datetime(2026, 11, 8, 3, 0, tzinfo=UTC))
REPLY = json.dumps(
    {
        "sentences": [
            {
                "text": "ACC-101 was paid by 7 distinct senders, at least 5 needed.",
                "fact_ids": ["S-FAN_IN"],
            }
        ]
    }
)


def closed_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port: int = probe.getsockname()[1]
    return port


def narrative_events(database: Path) -> dict[str, dict[str, object]]:
    with open_store(database) as store:
        log = AuditLog(store.connection)
        return {
            account: json.loads(event.payload)
            for account in ("ACC-101", "ACC-105", "ACC-106")
            for event in log.history(AccountId(account), AS_OF)
            if event.event_type is EventType.NARRATIVE
        }


def replay_run(database: Path, recordings: Path, rules: RulesConfig) -> None:
    run(
        GOLDEN / "payments.jsonl",
        GOLDEN / "profiles.jsonl",
        AS_OF,
        Settings(
            database,
            rules,
            database.parent / "out",
            CLOCK,
            ReplayClient(recordings, DEFAULT_MODEL),
        ),
    )


def run_command(tmp_path: Path, *flags: str) -> list[str]:
    return [
        "run",
        str(GOLDEN / "payments.jsonl"),
        str(GOLDEN / "profiles.jsonl"),
        "--as-of",
        "2026-11-03T00:00:00Z",
        "--db",
        str(tmp_path / "triage.db"),
        "--out",
        str(tmp_path / "out"),
        *flags,
    ]


def test_the_run_command_replays_recordings_by_default(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], acc_101: EvidencePack
) -> None:
    write_recording(
        tmp_path / "recorded",
        Recording(
            model=DEFAULT_MODEL,
            digest="sha256:5f2a9c",
            prompt_version=PROMPT_VERSION,
            facts_sha256=facts_sha256(acc_101),
            reply=REPLY,
        ),
    )
    args = run_command(tmp_path, "--recordings", str(tmp_path / "recorded"))

    exit_code = main(args, clock=CLOCK)

    assert exit_code == ExitCode.SUCCESS
    assert capsys.readouterr().out.splitlines()[8:10] == [
        "narratives: 1 drafted, 1 accepted",
        "fallen back: AI_OFF: 0, NEEDS_REVIEW: 1, NO_RECORDING: 1, AI_UNAVAILABLE: 0,"
        " REJECTED: 0",
    ]


def test_an_unreachable_model_degrades_the_run_and_still_stores_it(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    database = tmp_path / "triage.db"
    endpoint = f"http://127.0.0.1:{closed_port()}"
    args = run_command(tmp_path, "--ai", "live", "--endpoint", endpoint)

    exit_code = main(args, clock=CLOCK)

    assert exit_code == ExitCode.DEGRADED
    output = capsys.readouterr()
    assert "AI_UNAVAILABLE: 2" in output.out
    assert "AI_UNAVAILABLE" in output.err
    outcomes = {a: e["outcome"] for a, e in narrative_events(database).items()}
    assert outcomes == {
        "ACC-101": "AI_UNAVAILABLE",
        "ACC-105": "NEEDS_REVIEW",
        "ACC-106": "AI_UNAVAILABLE",
    }


def test_the_audit_records_a_draft_with_its_model_prompt_facts_reply_and_verdict(
    tmp_path: Path, rules: RulesConfig, acc_101: EvidencePack
) -> None:
    facts = facts_sha256(acc_101)
    label = ModelLabel(DEFAULT_MODEL, "sha256:5f2a9c", DraftSource.REPLAY)
    write_recording(
        tmp_path / "recorded",
        Recording(
            model=label.model,
            digest=label.digest,
            prompt_version=PROMPT_VERSION,
            facts_sha256=facts,
            reply=REPLY,
        ),
    )

    replay_run(tmp_path / "triage.db", tmp_path / "recorded", rules)

    event = narrative_events(tmp_path / "triage.db")["ACC-101"]
    assert event == {
        "run_id": 1,
        "version": 1,
        "outcome": "AI_DRAFT",
        "source": "replay",
        "model": DEFAULT_MODEL,
        "digest": "sha256:5f2a9c",
        "prompt_version": PROMPT_VERSION,
        "facts_sha256": facts,
        "reply": REPLY,
        "verdict": "ACCEPTED",
        "reasons": [],
        "note": None,
        "sentences": [
            {
                "text": "ACC-101 was paid by 7 distinct senders, at least 5 needed.",
                "fact_ids": ["S-FAN_IN"],
            }
        ],
    }


def test_the_audit_records_the_templates_note_and_sentences_when_there_is_no_draft(
    tmp_path: Path, rules: RulesConfig, acc_101: EvidencePack
) -> None:
    database = tmp_path / "triage.db"

    replay_run(database, tmp_path / "none", rules)

    event = narrative_events(database)["ACC-101"]
    assert event["note"] == NO_AI_DRAFT
    assert event["sentences"] == [
        {"text": sentence.text, "fact_ids": list(sentence.fact_ids)}
        for sentence in template_sentences(acc_101)
    ]


def test_a_missing_recording_falls_back_to_the_template_and_is_counted(
    tmp_path: Path, rules: RulesConfig
) -> None:
    database = tmp_path / "triage.db"

    result = run(
        GOLDEN / "payments.jsonl",
        GOLDEN / "profiles.jsonl",
        AS_OF,
        Settings(
            database,
            rules,
            tmp_path / "out",
            CLOCK,
            ReplayClient(tmp_path / "none", DEFAULT_MODEL),
        ),
    )

    assert {(n.account_id, n.outcome) for n in result.narratives} == {
        ("ACC-101", NarrativeOutcome.NO_RECORDING),
        ("ACC-105", NarrativeOutcome.NEEDS_REVIEW),
        ("ACC-106", NarrativeOutcome.NO_RECORDING),
    }
    reasons = narrative_events(database)["ACC-101"]["reasons"]
    assert isinstance(reasons, list) and reasons[0].startswith("NO_RECORDING: ")


@dataclass(frozen=True, slots=True)
class TimedClient:
    """Answers every prompt with REPLY, as a live call that took 3.456 seconds."""

    def draft(self, prompt: Prompt, facts_sha256: str) -> DraftResult:
        label = ModelLabel(DEFAULT_MODEL, "sha256:5f2a9c", DraftSource.LIVE)
        return Drafted(REPLY, label, latency_seconds=3.456)


def test_each_live_calls_latency_is_logged_without_its_account(
    tmp_path: Path, rules: RulesConfig, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger="mule_triage")

    run(
        GOLDEN / "payments.jsonl",
        GOLDEN / "profiles.jsonl",
        AS_OF,
        Settings(tmp_path / "triage.db", rules, tmp_path / "out", CLOCK, TimedClient()),
    )

    calls = [
        getattr(record, FIELDS)
        for record in caplog.records
        if record.getMessage() == "model_call"
    ]
    assert calls == [
        {"run_id": 1, "outcome": "AI_DRAFT", "seconds": 3.456},
        {"run_id": 1, "outcome": "REJECTED", "seconds": 3.456},
    ]


def test_a_rerun_that_changes_no_case_drafts_no_narrative(
    tmp_path: Path, rules: RulesConfig
) -> None:
    database = tmp_path / "triage.db"
    replay_run(database, tmp_path / "none", rules)

    result = run(
        GOLDEN / "payments.jsonl",
        GOLDEN / "profiles.jsonl",
        AS_OF,
        Settings(
            database,
            rules,
            tmp_path / "out",
            CLOCK,
            ReplayClient(tmp_path / "none", DEFAULT_MODEL),
        ),
    )

    assert result.narratives == ()
