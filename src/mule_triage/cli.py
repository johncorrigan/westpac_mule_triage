import argparse
import json
import logging
import re
import sqlite3
import sys
import time
import tomllib
from collections import Counter
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from decimal import Decimal
from enum import IntEnum
from pathlib import Path
from typing import assert_never

from pydantic import ValidationError

from mule_triage import pipeline
from mule_triage.clock import Clock
from mule_triage.contracts import (
    ACCOUNT_ID_PATTERN,
    AccountResult,
    AiMode,
    AiSettings,
    Alert,
    IngestCounts,
    NarrativeOutcome,
    NarrativeSummary,
    NoAlert,
    Outcome,
    RulesConfig,
    RunId,
    RunResult,
)
from mule_triage.errors import (
    ReconciliationError,
    RulesError,
    SettingsError,
    StoreError,
)
from mule_triage.narrative.client import (
    LiveClient,
    ModelClient,
    OffClient,
    ReplayClient,
)
from mule_triage.evidence import JsonObject, utc_iso
from mule_triage.logs import JsonLines, log_event
from mule_triage.narrative.prompt import PROMPT_VERSION
from mule_triage.pipeline import Settings
from mule_triage.report import case_markdown
from mule_triage.decision.review import (
    CaseNotFound,
    Disposed,
    Disposition,
    DispositionReason,
    NarrativeUse,
    Stale,
    case_view,
    dispose,
)
from mule_triage.detection.rules import unreachable_settings
from mule_triage.records.store import open_store

DEFAULT_RULES = Path("rules/rules-v1.toml")
DEFAULT_DATABASE = Path("out/mule_triage.db")
DEFAULT_OUT = Path("out/run")
PACKAGE_LOGGER = "mule_triage"
SUMMARY_FILE = "run_summary.json"

Runner = Callable[[Path, Path, datetime, Settings], RunResult]


class ExitCode(IntEnum):
    SUCCESS = 0
    UNEXPECTED_ERROR = 1
    USAGE_ERROR = 2
    RECONCILIATION_FAILED = 3
    DEGRADED = 4
    STALE_VERSION = 5
    DATABASE_UNAVAILABLE = 6


EXIT_MEANINGS = {
    ExitCode.SUCCESS: "the run is stored, and every case has its pack",
    ExitCode.UNEXPECTED_ERROR: "an unexpected error, with a traceback on stderr",
    ExitCode.USAGE_ERROR: (
        "an argument, input file, rules file or setting is wrong, or a file couldn't be"
        " read or written; the message on stderr says which"
    ),
    ExitCode.RECONCILIATION_FAILED: (
        "the stored rows don't account for every line read, so nothing was stored"
    ),
    ExitCode.DEGRADED: (
        "the run is stored, but the model was unavailable (AI_UNAVAILABLE), so those"
        " cases' narratives are templates"
    ),
    ExitCode.STALE_VERSION: (
        "the case changed after the reviewer read it, so the disposition wasn't recorded"
    ),
    ExitCode.DATABASE_UNAVAILABLE: "the database was locked or unreadable",
}


class WallClock:
    def now(self) -> datetime:
        return datetime.now(UTC)


def plain_id(text: str) -> str:
    if re.fullmatch(ACCOUNT_ID_PATTERN, text) is None:
        raise argparse.ArgumentTypeError(
            f"must be up to 64 letters, digits or _ . : -: {text!r}"
        )
    return text


def case_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("account", type=plain_id, help="the case's account ID")
    parser.add_argument(
        "--as-of", required=True, type=aware_time, help="the case's as-of time"
    )
    parser.add_argument("--db", type=Path, default=DEFAULT_DATABASE)


def aware_time(text: str) -> datetime:
    try:
        moment = datetime.fromisoformat(text)
    except ValueError as error:
        raise argparse.ArgumentTypeError(f"isn't an ISO 8601 time: {text}") from error
    if moment.tzinfo is None:
        raise argparse.ArgumentTypeError(
            f"needs a timezone, such as 2026-11-03T00:00:00Z: {text}"
        )
    return moment.astimezone(UTC)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="mule_triage",
        description=(
            "Triage possible money-mule accounts in one day of synthetic payments."
        ),
    )
    commands = parser.add_subparsers(dest="command")
    run = commands.add_parser(
        "run", help="ingest a day's files and give every account an outcome"
    )
    run.add_argument("payments", type=Path, help="payments, as JSON Lines")
    run.add_argument("profiles", type=Path, help="account profiles, as JSON Lines")
    run.add_argument(
        "--as-of",
        required=True,
        type=aware_time,
        help="the time the run is judged at, with a timezone",
    )
    run.add_argument("--rules", type=Path, default=DEFAULT_RULES)
    run.add_argument("--db", type=Path, default=DEFAULT_DATABASE)
    run.add_argument("--out", type=Path, default=DEFAULT_OUT)
    ai_arguments(run)
    show = commands.add_parser(
        "show", help="show one case, its version, its evidence and its history"
    )
    case_arguments(show)
    show.add_argument("--rules", type=Path, default=DEFAULT_RULES)
    disposal = commands.add_parser(
        "dispose", help="record a reviewer's disposition of one case"
    )
    case_arguments(disposal)
    disposal.add_argument(
        "--version",
        required=True,
        type=int,
        help="the case version the reviewer saw, as `show` gives it",
    )
    disposal.add_argument(
        "--reason", required=True, type=DispositionReason, choices=DispositionReason
    )
    disposal.add_argument("--reviewer", required=True, type=plain_id)
    disposal.add_argument(
        "--narrative",
        required=True,
        type=NarrativeUse,
        choices=NarrativeUse,
        help="whether the reviewer accepted, edited or discarded the narrative",
    )
    return parser


def ai_arguments(parser: argparse.ArgumentParser) -> None:
    defaults = AiSettings()
    parser.add_argument(
        "--ai",
        type=AiMode,
        choices=list(AiMode),
        default=defaults.mode,
        help="draft narratives with no model, from recorded replies, or live",
    )
    parser.add_argument(
        "--record",
        action="store_true",
        help="with --ai live, save each reply under its key in --recordings",
    )
    parser.add_argument("--model", default=defaults.model)
    parser.add_argument("--endpoint", default=defaults.endpoint, help="Ollama's URL")
    parser.add_argument("--ai-timeout", type=int, default=defaults.timeout_seconds)
    parser.add_argument("--recordings", type=Path, default=defaults.recordings)


def ai_settings(args: argparse.Namespace) -> AiSettings:
    try:
        return AiSettings(
            mode=args.ai,
            model=args.model,
            endpoint=args.endpoint,
            timeout_seconds=args.ai_timeout,
            record=args.record,
            recordings=args.recordings,
        )
    except ValidationError as error:
        problems = "; ".join(
            f"{'.'.join(str(part) for part in detail['loc']) or 'ai'}: {detail['msg']}"
            for detail in error.errors()
        )
        raise SettingsError(f"AI settings: {problems}") from error


def model_client(ai: AiSettings) -> ModelClient:
    match ai.mode:
        case AiMode.OFF:
            return OffClient()
        case AiMode.REPLAY:
            return ReplayClient(ai.recordings, ai.model)
        case AiMode.LIVE:
            record_to = ai.recordings if ai.record else None
            return LiveClient(ai.endpoint, ai.model, ai.timeout_seconds, record_to)
        case _:
            assert_never(ai.mode)


def load_rules(path: Path) -> RulesConfig:
    try:
        with path.open("rb") as handle:
            data = tomllib.load(handle, parse_float=Decimal)
    except OSError as error:
        raise RulesError(f"{path}: can't be read ({error.strerror})") from error
    except tomllib.TOMLDecodeError as error:
        raise RulesError(f"{path}: isn't valid TOML ({error})") from error
    try:
        rules = RulesConfig.model_validate(data)
    except ValidationError as error:
        problems = "; ".join(
            f"{'.'.join(str(part) for part in detail['loc']) or 'rules'}: "
            f"{detail['type']}"
            for detail in error.errors()
        )
        raise RulesError(f"{path}: {problems}") from error
    unreachable = unreachable_settings(rules)
    if unreachable:
        raise RulesError(f"{path}: {'; '.join(unreachable)}")
    return rules


@dataclass(frozen=True, slots=True)
class RunSummary:
    """What a run did, printed and saved as JSON. It holds no times or durations, so the
    same inputs and database always give the same summary; those go in the logs."""

    result: RunResult
    as_of: datetime
    rules_version: str
    prompt_version: str
    ai_mode: AiMode
    exit_code: ExitCode


@dataclass(frozen=True, slots=True)
class AiCounts:
    """`drafted` counts the replies the model gave, accepted by the validator or not; every
    narrative but an accepted one falls back to the template, for its reason."""

    drafted: int
    accepted: int
    fallen_back: tuple[tuple[NarrativeOutcome, int], ...]
    unavailable: int


def ai_counts(narratives: tuple[NarrativeSummary, ...]) -> AiCounts:
    counts = Counter(each.outcome for each in narratives)
    accepted = counts[NarrativeOutcome.AI_DRAFT]
    return AiCounts(
        drafted=accepted + counts[NarrativeOutcome.REJECTED],
        accepted=accepted,
        fallen_back=tuple(
            (outcome, counts[outcome])
            for outcome in NarrativeOutcome
            if outcome is not NarrativeOutcome.AI_DRAFT
        ),
        unavailable=counts[NarrativeOutcome.AI_UNAVAILABLE],
    )


def run_exit_code(result: RunResult) -> ExitCode:
    unavailable = ai_counts(result.narratives).unavailable
    return ExitCode.DEGRADED if unavailable else ExitCode.SUCCESS


def exit_meaning(code: ExitCode) -> str:
    return f"{code.name}: {EXIT_MEANINGS[code]}"


def flagged(result: RunResult) -> list[AccountResult]:
    return [account for account in result.accounts if not isinstance(account, NoAlert)]


def incomplete_alerts(result: RunResult) -> int:
    return sum(
        isinstance(account, Alert) and account.incomplete for account in result.accounts
    )


def flagged_document(account: AccountResult) -> JsonObject:
    alert = account if isinstance(account, Alert) else None
    return {
        "account_id": account.account_id,
        "outcome": account.outcome.value,
        "priority": None if alert is None else alert.priority.value,
        "incomplete": alert is not None and alert.incomplete,
        "reasons": [reason.value for reason in sorted(account.reasons)],
    }


def summary_document(summary: RunSummary) -> JsonObject:
    result = summary.result
    outcomes = Counter(account.outcome for account in result.accounts)
    ai = ai_counts(result.narratives)
    return {
        "run_id": result.run_id,
        "as_of": utc_iso(summary.as_of),
        "rules_version": summary.rules_version,
        "prompt_version": summary.prompt_version,
        "ai_mode": summary.ai_mode.value,
        "payments": asdict(result.payment_counts),
        "profiles": asdict(result.profile_counts),
        "outcomes": {outcome.value: outcomes[outcome] for outcome in Outcome},
        "incomplete_alerts": incomplete_alerts(result),
        "flagged_accounts": [flagged_document(account) for account in flagged(result)],
        "cases": asdict(result.cases),
        "narratives": {
            "drafted": ai.drafted,
            "accepted": ai.accepted,
            "fallen_back": {outcome.value: count for outcome, count in ai.fallen_back},
            "ai_unavailable": ai.unavailable,
        },
        "exit_code": int(summary.exit_code),
        "exit_meaning": exit_meaning(summary.exit_code),
    }


def counts_text(counts: IngestCounts) -> str:
    return (
        f"{counts.received} received, {counts.accepted} accepted,"
        f" {counts.rejected} rejected, {counts.duplicates} duplicates"
    )


def flagged_line(account: AccountResult) -> str:
    flags = [account.outcome.value]
    if isinstance(account, Alert):
        flags.append(account.priority.value)
        if account.incomplete:
            flags.append("INCOMPLETE")
    reasons = ",".join(sorted(account.reasons))
    return f"{account.account_id} {' '.join(flags)} {reasons}"


def summary_text(summary: RunSummary) -> str:
    result, code, cases = summary.result, summary.exit_code, summary.result.cases
    outcomes = Counter(account.outcome for account in result.accounts)
    ai = ai_counts(result.narratives)
    lines = [
        f"run {result.run_id}, as of {utc_iso(summary.as_of)}:"
        f" rules {summary.rules_version}, prompt {summary.prompt_version},"
        f" AI {summary.ai_mode}",
        f"payments: {counts_text(result.payment_counts)}",
        f"profiles: {counts_text(result.profile_counts)}",
        "outcomes: "
        + ", ".join(f"{outcome}: {outcomes[outcome]}" for outcome in Outcome)
        + f"; INCOMPLETE alerts: {incomplete_alerts(result)}",
        *(flagged_line(account) for account in flagged(result)),
        f"cases: {cases.created} created, {cases.changed} changed,"
        f" {cases.unchanged} unchanged",
        f"narratives: {ai.drafted} drafted, {ai.accepted} accepted",
        "fallen back: "
        + ", ".join(f"{outcome}: {count}" for outcome, count in ai.fallen_back),
        f"{NarrativeOutcome.AI_UNAVAILABLE}: {ai.unavailable}",
        f"exit code {int(code)} ({code.name}): {EXIT_MEANINGS[code]}",
    ]
    return "\n".join(lines)


def write_summary(folder: Path, summary: RunSummary) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    text = json.dumps(summary_document(summary), indent=2, ensure_ascii=True)
    (folder / SUMMARY_FILE).write_text(text + "\n", encoding="utf-8", newline="\n")


def dispose_command(args: argparse.Namespace, clock: Clock) -> int:
    disposition = Disposition(
        account_id=args.account,
        as_of=args.as_of,
        seen_version=args.version,
        reason=args.reason,
        reviewer=args.reviewer,
        narrative=args.narrative,
    )
    with open_store(args.db) as store:
        result = dispose(store, disposition, clock)
    case = f"{args.account} at {args.as_of.isoformat()}"
    match result:
        case Disposed(version=version):
            print(f"{case}: {args.reason}, and closed at version {version}")
            return ExitCode.SUCCESS
        case Stale():
            print(f"mule_triage: {case}: {result.message}", file=sys.stderr)
            return ExitCode.STALE_VERSION
        case CaseNotFound():
            print(f"mule_triage: {case}: no such case", file=sys.stderr)
            return ExitCode.USAGE_ERROR
        case _:
            assert_never(result)


def show_command(args: argparse.Namespace, rules: RulesConfig) -> int:
    with open_store(args.db) as store:
        view = case_view(store, args.account, args.as_of, rules)
    if view is None:
        case = f"{args.account} at {args.as_of.isoformat()}"
        print(f"mule_triage: {case}: no such case", file=sys.stderr)
        return ExitCode.USAGE_ERROR
    print(case_markdown(view), end="")
    return ExitCode.SUCCESS


def main(
    argv: Sequence[str] | None = None,
    run: Runner = pipeline.run,
    clock: Clock | None = None,
) -> int:
    """A locked or unreadable database raises sqlite3.DatabaseError, which is reported here;
    each command's open transaction has rolled back by then."""
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command is None:
        parser.print_help()
        return ExitCode.SUCCESS
    now = WallClock() if clock is None else clock
    with json_logs_on_stderr():
        if args.command == "run":
            return run_command(args, run, now)
        try:
            return review_command(args, now)
        except sqlite3.DatabaseError as error:
            return database_unavailable(args.db, error)


@contextmanager
def json_logs_on_stderr() -> Iterator[None]:
    """For one call of `main` only, so a second call doesn't write each record twice."""
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(JsonLines())
    package = logging.getLogger(PACKAGE_LOGGER)
    level = package.level
    package.addHandler(handler)
    package.setLevel(logging.INFO)
    try:
        yield
    finally:
        package.removeHandler(handler)
        package.setLevel(level)


def database_unavailable(database: Path, error: sqlite3.DatabaseError) -> ExitCode:
    print(
        f"mule_triage: {database}: {error}; nothing was changed, so try again once"
        " the database is free, or use another --db",
        file=sys.stderr,
    )
    return ExitCode.DATABASE_UNAVAILABLE


def log_level(code: ExitCode) -> int:
    if code is ExitCode.SUCCESS:
        return logging.INFO
    return logging.WARNING if code is ExitCode.DEGRADED else logging.ERROR


def run_command(args: argparse.Namespace, run: Runner, now: Clock) -> ExitCode:
    """Logs how the run ended, even when an unexpected error escapes as exit code 1."""
    started = time.perf_counter()
    code: ExitCode = ExitCode.UNEXPECTED_ERROR
    run_id: RunId | None = None
    try:
        code, run_id = triage_command(args, run, now)
    finally:
        log_event(
            __name__,
            log_level(code),
            "run_finished",
            run_id,
            exit_code=int(code),
            exit_meaning=exit_meaning(code),
            seconds=round(time.perf_counter() - started, 3),
        )
    return code


def triage_command(
    args: argparse.Namespace, run: Runner, now: Clock
) -> tuple[ExitCode, RunId | None]:
    """The exit code, and the run's ID once a summary shows it was stored."""
    try:
        rules = load_rules(args.rules)
        ai = ai_settings(args)
    except (RulesError, SettingsError) as error:
        print(f"mule_triage: {error}", file=sys.stderr)
        return ExitCode.USAGE_ERROR, None
    try:
        args.db.parent.mkdir(parents=True, exist_ok=True)
        settings = Settings(args.db, rules, args.out, now, model_client(ai))
        result = run(args.payments, args.profiles, args.as_of, settings)
        summary = RunSummary(
            result,
            args.as_of,
            rules.version,
            PROMPT_VERSION,
            ai.mode,
            run_exit_code(result),
        )
        write_summary(args.out, summary)
    except ReconciliationError as error:
        print(f"mule_triage: nothing was stored: {error}", file=sys.stderr)
        return ExitCode.RECONCILIATION_FAILED, None
    except StoreError as error:
        print(f"mule_triage: {error}", file=sys.stderr)
        return ExitCode.USAGE_ERROR, None
    except OSError as error:
        print(f"mule_triage: {error.filename}: {error.strerror}", file=sys.stderr)
        return ExitCode.USAGE_ERROR, None
    except sqlite3.DatabaseError as error:
        return database_unavailable(args.db, error), None
    print(summary_text(summary))
    return summary.exit_code, result.run_id


def review_command(args: argparse.Namespace, now: Clock) -> int:
    if not args.db.is_file():
        print(f"mule_triage: {args.db}: no database there", file=sys.stderr)
        return ExitCode.USAGE_ERROR
    try:
        if args.command == "dispose":
            return dispose_command(args, now)
        return show_command(args, load_rules(args.rules))
    except (RulesError, StoreError) as error:
        print(f"mule_triage: {error}", file=sys.stderr)
        return ExitCode.USAGE_ERROR
