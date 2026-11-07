"""Run every gate in order and print a pass/fail table.

    python scripts/verify.py          every gate
    python scripts/verify.py --fast   black, pyflakes, the golden checksum and the secrets check

The commit hook runs --fast. Standard library only.
"""

import sys

# First, so an older Python stops with this message rather than an ImportError below.
if sys.version_info < (3, 11):
    sys.exit(
        f"verify: Python 3.11 or newer is needed, but this is Python "
        f"{sys.version.split()[0]}. Activate .venv, or run python3 scripts/quickstart.py."
    )

import argparse
import hashlib
import importlib.util
import json
import os
import re
import subprocess
import tempfile
import time
import tomllib
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from importlib import metadata
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
INSTALL_COMMAND = 'python -m pip install -c constraints.txt -e ".[dev]"'
CODE_DIRS = ("src", "tests", "scripts")
COVERAGE_FLOOR = 80
TOOL_TIMEOUT_SECONDS = 600
GOLDEN_EXPECTED = ROOT / "tests" / "golden" / "expected"
GOLDEN_MANIFEST = "SHA256SUMS"
GOLDEN_IGNORED = frozenset({".DS_Store"})
GOLDEN_CODE_SUFFIXES = frozenset({".py", ".pyc"})
SMOKE_INPUTS = ("tests/golden/payments.jsonl", "tests/golden/profiles.jsonl")
SMOKE_AS_OF = "2026-11-03T00:00:00Z"
SMOKE_STAMP = "20261103T000000Z"
SMOKE_SUMMARY = "run_summary.json"
SMOKE_COUNTS = ("received", "accepted", "rejected", "duplicates")
MANIFEST_LINE = re.compile(r"([0-9a-fA-F]{64}) [ *](.+)")
REQUIREMENT_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")
ENV_FILE = re.compile(r"(^|/)\.env(\.[^/]+)?$", re.IGNORECASE)
ENV_TEMPLATE = re.compile(r"(^|/)\.env\.example$", re.IGNORECASE)
SECRET_PATTERNS = (
    ("private key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("AWS access key ID", re.compile(r"\b(AKIA|ASIA)[0-9A-Z]{16}\b")),
    ("GitHub token", re.compile(r"\b(gh[pousr]_[A-Za-z0-9]{36,}|github_pat_\w{22,})")),
    ("Slack token", re.compile(r"\bxox[abposr]-[A-Za-z0-9-]{10,}")),
    ("API key", re.compile(r"\bsk-[A-Za-z0-9_-]{20,}")),
    (
        "hard-coded credential",
        re.compile(
            r"\b(password|passwd|secret|api_?key|access_?token)\b['\"]?\s*[:=]\s*"
            r"['\"][^'\"\s]{8,}['\"]",
            re.IGNORECASE,
        ),
    ),
)


class Status(StrEnum):
    PASS = "PASS"
    FAIL = "FAIL"


@dataclass(frozen=True, slots=True)
class Outcome:
    status: Status
    detail: str


@dataclass(frozen=True, slots=True)
class Gate:
    name: str
    fast: bool
    run: Callable[[], Outcome]


@dataclass(frozen=True, slots=True)
class GateResult:
    name: str
    outcome: Outcome
    seconds: float


def say(text: str) -> None:
    print(text.encode("ascii", "replace").decode("ascii"), flush=True)


def normalise(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def direct_requirements() -> list[str]:
    with (ROOT / "pyproject.toml").open("rb") as handle:
        project = tomllib.load(handle)["project"]
    specifiers = list(project["dependencies"])
    for extra in project.get("optional-dependencies", {}).values():
        specifiers.extend(extra)
    names = [str(project["name"])]
    for specifier in specifiers:
        match = REQUIREMENT_NAME.match(str(specifier))
        if match is not None:
            names.append(match.group(0))
    return names


def pinned_versions() -> dict[str, str]:
    pins: dict[str, str] = {}
    lines = (ROOT / "constraints.txt").read_text(encoding="utf-8").splitlines()
    for line in lines:
        text = line.split("#", 1)[0].strip()
        if "==" in text:
            name, version = text.split("==", 1)
            pins[normalise(name)] = version.strip()
    return pins


def installed_versions() -> dict[str, str]:
    versions: dict[str, str] = {}
    for distribution in metadata.distributions():
        name = distribution.metadata["Name"]
        if name:
            versions.setdefault(normalise(name), distribution.version)
    return versions


def pin_problems(pins: dict[str, str], installed: dict[str, str]) -> list[str]:
    return [
        f"{name} {installed[name]} is installed, but constraints.txt pins {version}."
        for name, version in sorted(pins.items())
        if name in installed and installed[name] != version
    ]


def source_problem() -> str | None:
    spec = importlib.util.find_spec("mule_triage")
    if spec is None or spec.origin is None:
        return None
    loaded_from = Path(spec.origin).resolve().parent
    if loaded_from != (ROOT / "src" / "mule_triage").resolve():
        return f"mule_triage loads from {loaded_from}, not from this checkout's src/."
    return None


def environment_problems() -> list[str]:
    if not (ROOT / "constraints.txt").is_file():
        return ["constraints.txt is missing."]
    installed = installed_versions()
    problems = [
        f"{name} isn't installed."
        for name in direct_requirements()
        if normalise(name) not in installed
    ]
    problems.extend(pin_problems(pinned_versions(), installed))
    location = source_problem()
    if location is not None:
        problems.append(location)
    return problems


def run_module(args: list[str]) -> tuple[int, str]:
    environment = dict(os.environ, PYTHONUTF8="1")
    try:
        done = subprocess.run(
            [sys.executable, "-m", *args],
            cwd=ROOT,
            env=environment,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=TOOL_TIMEOUT_SECONDS,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return -1, f"timed out after {TOOL_TIMEOUT_SECONDS} seconds"
    return done.returncode, (done.stdout + done.stderr).strip()


def last_line(text: str) -> str:
    lines = [line for line in text.splitlines() if line.strip()]
    return lines[-1].strip() if lines else ""


def tool_gate(*args: str) -> Callable[[], Outcome]:
    def gate() -> Outcome:
        code, output = run_module(list(args))
        if code == 0:
            return Outcome(Status.PASS, last_line(output))
        return Outcome(Status.FAIL, f"exit code {code}\n{output}")

    return gate


def smoke_problems(out: Path, answer_key: Path) -> list[str]:
    """A fresh run's summary must show the answer key's payment counts, and a case and its
    two pack files for each account the key doesn't give its default outcome."""
    summary_path = out / SMOKE_SUMMARY
    if not summary_path.is_file():
        return [f"{SMOKE_SUMMARY} is missing"]
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    with answer_key.open("rb") as handle:
        key = tomllib.load(handle)
    problems: list[str] = []
    payments = {name: key["ingest"][name] for name in SMOKE_COUNTS}
    if summary.get("payments") != payments:
        problems.append(f"payments are {summary.get('payments')}, not {payments}")
    cases = sorted(
        account
        for account, expected in key["accounts"].items()
        if expected["outcome"] != key["default_outcome"]
    )
    created = {"created": len(cases), "changed": 0, "unchanged": 0}
    if summary.get("cases") != created:
        problems.append(f"cases are {summary.get('cases')}, not {created}")
    packs = sorted(path.name for path in out.iterdir() if path.name != SMOKE_SUMMARY)
    wanted = sorted(
        f"{account}_{SMOKE_STAMP}.{suffix}"
        for account in cases
        for suffix in ("json", "md")
    )
    if packs != wanted:
        problems.append(f"packs are {packs}, not {wanted}")
    return problems


def check_smoke() -> Outcome:
    """The golden day, replayed through the command line into a new database and folder."""
    with tempfile.TemporaryDirectory() as folder:
        out = Path(folder) / "out"
        code, output = run_module(
            [
                "mule_triage",
                "run",
                *SMOKE_INPUTS,
                "--as-of",
                SMOKE_AS_OF,
                "--db",
                str(Path(folder) / "smoke.db"),
                "--out",
                str(out),
                "--ai",
                "replay",
            ]
        )
        if code != 0:
            return Outcome(Status.FAIL, f"exit code {code}\n{output}")
        problems = smoke_problems(out, GOLDEN_EXPECTED / "outcomes.toml")
    if problems:
        return Outcome(Status.FAIL, "\n".join(problems))
    return Outcome(
        Status.PASS, "golden replay: the answer key's payment counts, a pack per case"
    )


def sha256_of(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def needs_a_checksum(path: Path, golden: Path) -> bool:
    return not (
        path.suffix in GOLDEN_CODE_SUFFIXES
        or "__pycache__" in path.relative_to(golden).parts
        or path.name in GOLDEN_IGNORED
    )


def check_golden(expected: Path) -> Outcome:
    """The manifest lists paths relative to the golden folder, which holds the answer key.

    Every file in that folder except Python code must be listed.
    """
    if not expected.is_dir():
        return Outcome(Status.FAIL, "tests/golden/expected/ is missing")
    expected = expected.resolve()
    golden = expected.parent
    manifest = expected / GOLDEN_MANIFEST
    if not manifest.is_file():
        return Outcome(Status.FAIL, f"{GOLDEN_MANIFEST} is missing from the answer key")
    listed: dict[str, str] = {}
    lines = manifest.read_text(encoding="utf-8").splitlines()
    for number, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        match = MANIFEST_LINE.fullmatch(line.rstrip())
        if match is None:
            return Outcome(
                Status.FAIL, f"{GOLDEN_MANIFEST} line {number} isn't '<sha256>  <path>'"
            )
        listed[match.group(2).removeprefix("./")] = match.group(1).lower()
    if not listed:
        return Outcome(Status.FAIL, f"{GOLDEN_MANIFEST} lists no files")
    present = {
        path.relative_to(golden).as_posix()
        for path in golden.rglob("*")
        if path.is_file() and path != manifest and needs_a_checksum(path, golden)
    }
    problems = [f"{name} isn't listed" for name in sorted(present - listed.keys())]
    for name, digest in sorted(listed.items()):
        target = (golden / name).resolve()
        if not target.is_relative_to(golden):
            problems.append(f"{name} is outside the golden folder")
        elif not target.is_file():
            problems.append(f"{name} is missing")
        elif sha256_of(target) != digest:
            problems.append(f"{name} has changed")
    if problems:
        return Outcome(Status.FAIL, "; ".join(problems))
    return Outcome(Status.PASS, f"{len(listed)} files match {GOLDEN_MANIFEST}")


def unignored_files(root: Path) -> list[str] | None:
    try:
        done = subprocess.run(
            ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
            cwd=root,
            capture_output=True,
            timeout=60,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if done.returncode != 0:
        return None
    names = done.stdout.decode("utf-8", errors="replace").split("\0")
    return [name for name in names if name]


def secrets_in(root: Path, relative: str) -> list[str]:
    if ENV_FILE.search(relative) and not ENV_TEMPLATE.search(relative):
        return [f"{relative}: a .env file isn't ignored"]
    path = root / relative
    if not path.is_file():
        return []
    data = path.read_bytes()
    if b"\0" in data:
        return []
    findings: list[str] = []
    text = data.decode("utf-8", errors="replace")
    for number, line in enumerate(text.splitlines(), start=1):
        for kind, pattern in SECRET_PATTERNS:
            if pattern.search(line):
                findings.append(f"{relative}:{number}: looks like a {kind}")
    return findings


def check_secrets(root: Path) -> Outcome:
    files = unignored_files(root)
    if files is None:
        return Outcome(Status.FAIL, "git ls-files failed, so no files were checked")
    findings = [finding for relative in files for finding in secrets_in(root, relative)]
    if findings:
        return Outcome(Status.FAIL, "\n".join(findings))
    return Outcome(Status.PASS, f"{len(files)} files checked")


GATES = (
    Gate("black", True, tool_gate("black", "--check", *CODE_DIRS)),
    Gate("pyflakes", True, tool_gate("pyflakes", *CODE_DIRS)),
    Gate("mypy", False, tool_gate("mypy")),
    Gate(
        "pytest",
        False,
        tool_gate(
            "pytest", "-q", "--cov=mule_triage", f"--cov-fail-under={COVERAGE_FLOOR}"
        ),
    ),
    Gate("golden checksum", True, lambda: check_golden(GOLDEN_EXPECTED)),
    Gate("secrets", True, lambda: check_secrets(ROOT)),
    Gate("smoke", False, check_smoke),
)


def run_gates(gates: list[Gate]) -> list[GateResult]:
    results: list[GateResult] = []
    for gate in gates:
        say(f"verify: running {gate.name}")
        start = time.monotonic()
        outcome = gate.run()
        results.append(GateResult(gate.name, outcome, time.monotonic() - start))
    return results


def report(results: list[GateResult], mode: str) -> None:
    failed = [result for result in results if result.outcome.status is Status.FAIL]
    for result in failed:
        say(f"\n--- {result.name} ---\n{result.outcome.detail}")
    width = max(len("Gate"), *(len(result.name) for result in results))
    say("")
    say(f"{'Gate':<{width}}  Result  Seconds  Detail")
    say(f"{'-' * width}  ------  -------  ------")
    for result in results:
        lines = result.outcome.detail.splitlines()
        summary = lines[0] if lines else ""
        status = result.outcome.status
        say(f"{result.name:<{width}}  {status:<6}  {result.seconds:7.1f}  {summary}")
    say("")
    if failed:
        say(f"{mode}: FAILED ({', '.join(result.name for result in failed)})")
    else:
        say(f"{mode}: every gate passed")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the project's gates.")
    parser.add_argument(
        "--fast",
        action="store_true",
        help="run only black, pyflakes, the golden checksum and the secrets check",
    )
    args = parser.parse_args(argv)
    problems = environment_problems()
    if problems:
        say("verify: the environment isn't ready:")
        for problem in problems:
            say(f"  - {problem}")
        if sys.prefix == sys.base_prefix:
            say("  This Python isn't a virtual environment: activate .venv first.")
        say(f"  To fix it, run: {INSTALL_COMMAND}")
        say("  or set everything up with: python3 scripts/quickstart.py")
        return 2
    gates = [gate for gate in GATES if gate.fast or not args.fast]
    results = run_gates(gates)
    report(results, "verify --fast" if args.fast else "verify")
    return 1 if any(result.outcome.status is Status.FAIL for result in results) else 0


if __name__ == "__main__":
    sys.exit(main())
