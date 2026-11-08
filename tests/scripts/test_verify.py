import hashlib
import importlib.util
import json
import sys
from collections.abc import Callable
from pathlib import Path
from types import ModuleType

import pytest

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
# Built from pieces, so the secrets gate doesn't flag this file itself.
KEY = "pass" + "word"
PRIVATE_KEY_HEADER = "-----BEGIN " + "PRIVATE KEY-----"


@pytest.fixture(scope="module")
def verify() -> ModuleType:
    spec = importlib.util.spec_from_file_location("verify", SCRIPTS / "verify.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["verify"] = module
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize(
    "line",
    [f'{KEY} = "hunter2hunter2"', f'{{"{KEY}": "hunter2hunter2"}}', PRIVATE_KEY_HEADER],
    ids=["python assignment", "json key", "private key"],
)
def test_flags_a_file_holding_a_secret(
    verify: ModuleType, tmp_path: Path, line: str
) -> None:
    (tmp_path / "config.txt").write_text(line + "\n", encoding="utf-8")

    findings = verify.secrets_in(tmp_path, "config.txt")

    assert len(findings) == 1
    assert findings[0].startswith("config.txt:1: looks like a")


@pytest.mark.parametrize(
    "line",
    ["amount_cents = 500", "api_key: SecretStr", f'{KEY} = ""'],
    ids=["plain code", "type hint", "empty value"],
)
def test_passes_a_file_without_secrets(
    verify: ModuleType, tmp_path: Path, line: str
) -> None:
    (tmp_path / "config.txt").write_text(line + "\n", encoding="utf-8")

    findings = verify.secrets_in(tmp_path, "config.txt")

    assert findings == []


def test_reports_an_installed_version_that_differs_from_its_pin(
    verify: ModuleType,
) -> None:
    pins = {"black": "26.10.1"}

    problems = verify.pin_problems(pins, {"black": "25.1.0"})

    assert problems == ["black 25.1.0 is installed, but constraints.txt pins 26.10.1."]


def test_ignores_a_pinned_package_that_this_platform_doesnt_install(
    verify: ModuleType,
) -> None:
    pins = {"colorama": "0.4.6"}

    problems = verify.pin_problems(pins, {})

    assert problems == []


EMPTY = b"{}\n"
EMPTY_DIGEST = hashlib.sha256(EMPTY).hexdigest()


def write_answer_key(golden: Path) -> Path:
    expected = golden / "expected"
    expected.mkdir(parents=True)
    (expected / "outcomes.json").write_bytes(EMPTY)
    (golden / "payments.jsonl").write_bytes(EMPTY)
    (expected / "SHA256SUMS").write_bytes(
        f"{EMPTY_DIGEST}  expected/outcomes.json\n"
        f"{EMPTY_DIGEST}  payments.jsonl\n".encode()
    )
    return expected


def list_a_file_outside_the_golden_folder(key: Path, listed_as: str) -> None:
    (key.parent.parent / "outside.json").write_bytes(EMPTY)
    with (key / "SHA256SUMS").open("a", encoding="utf-8") as manifest:
        manifest.write(f"{EMPTY_DIGEST}  {listed_as}\n")


def test_passes_an_answer_key_and_data_that_match_their_checksums(
    verify: ModuleType, tmp_path: Path
) -> None:
    key = write_answer_key(tmp_path / "golden")

    outcome = verify.check_golden(key)

    assert outcome.status == verify.Status.PASS
    assert outcome.detail == "2 files match SHA256SUMS"


def test_ignores_unlisted_python_files_in_the_golden_folder(
    verify: ModuleType, tmp_path: Path
) -> None:
    key = write_answer_key(tmp_path / "golden")
    (tmp_path / "golden" / "test_golden.py").write_bytes(b"")
    (tmp_path / "golden" / "__pycache__").mkdir()
    (tmp_path / "golden" / "__pycache__" / "test_golden.cpython-314.pyc").write_bytes(
        b""
    )

    outcome = verify.check_golden(key)

    assert outcome.status == verify.Status.PASS


def test_fails_when_there_is_no_answer_key(verify: ModuleType, tmp_path: Path) -> None:
    outcome = verify.check_golden(tmp_path / "golden" / "expected")

    assert outcome.status == verify.Status.FAIL
    assert outcome.detail == "tests/golden/expected/ is missing"


@pytest.mark.parametrize(
    ("tamper", "reason"),
    [
        (
            lambda key: (key / "outcomes.json").write_bytes(b"[]\n"),
            "expected/outcomes.json has changed",
        ),
        (
            lambda key: (key.parent / "payments.jsonl").write_bytes(b"[]\n"),
            "payments.jsonl has changed",
        ),
        (
            lambda key: (key / "extra.json").write_bytes(EMPTY),
            "expected/extra.json isn't listed",
        ),
        (
            lambda key: (key / "SHA256SUMS").write_bytes(
                f"{EMPTY_DIGEST}  expected/outcomes.json\n".encode()
            ),
            "payments.jsonl isn't listed",
        ),
        (
            lambda key: (key.parent / "notes.md").write_bytes(EMPTY),
            "notes.md isn't listed",
        ),
        (
            lambda key: (key / "outcomes.json").unlink(),
            "expected/outcomes.json is missing",
        ),
        (
            lambda key: (key.parent / "payments.jsonl").unlink(),
            "payments.jsonl is missing",
        ),
        (
            lambda key: list_a_file_outside_the_golden_folder(key, "../outside.json"),
            "../outside.json is outside the golden folder",
        ),
        (
            lambda key: list_a_file_outside_the_golden_folder(
                key, str(key.parent.parent / "outside.json")
            ),
            "outside.json is outside the golden folder",
        ),
        (lambda key: (key / "SHA256SUMS").unlink(), "SHA256SUMS is missing"),
        (lambda key: (key / "SHA256SUMS").write_bytes(b"not a checksum\n"), "line 1"),
        (lambda key: (key / "SHA256SUMS").write_bytes(b"\n"), "lists no files"),
    ],
    ids=[
        "changed answer key",
        "changed data file",
        "unlisted file in the answer key",
        "data file dropped from the checksums",
        "unlisted file beside the answer key",
        "missing answer key",
        "missing data file",
        "path above the golden folder",
        "absolute path outside the golden folder",
        "missing checksums",
        "malformed line",
        "empty checksums",
    ],
)
def test_fails_a_golden_file_that_doesnt_match_its_checksums(
    verify: ModuleType,
    tmp_path: Path,
    tamper: Callable[[Path], object],
    reason: str,
) -> None:
    key = write_answer_key(tmp_path / "golden")
    tamper(key)

    outcome = verify.check_golden(key)

    assert outcome.status == verify.Status.FAIL
    assert reason in outcome.detail


SMOKE_KEY = """default_outcome = "NO_ALERT"

[ingest]
received = 3
accepted = 2
rejected = 1
duplicates = 0

[accounts.ACC-1]
outcome = "ALERT"

[accounts.ACC-2]
outcome = "NO_ALERT"
"""
SMOKE_SUMMARY = {
    "payments": {"received": 3, "accepted": 2, "rejected": 1, "duplicates": 0},
    "cases": {"created": 1, "changed": 0, "unchanged": 0},
}


def write_smoke_run(folder: Path) -> tuple[Path, Path]:
    """A smoke run's output folder and the answer key it should match."""
    key = folder / "outcomes.toml"
    key.write_text(SMOKE_KEY, encoding="utf-8")
    out = folder / "out"
    out.mkdir()
    (out / "run_summary.json").write_text(json.dumps(SMOKE_SUMMARY), encoding="utf-8")
    for suffix in ("md", "json"):
        (out / f"ACC-1_20261103T000000Z.{suffix}").write_text("", encoding="utf-8")
    return out, key


def rewrite_summary(out: Path, section: str, value: dict[str, int]) -> None:
    (out / "run_summary.json").write_text(
        json.dumps({**SMOKE_SUMMARY, section: value}), encoding="utf-8"
    )


def test_passes_a_smoke_run_that_matches_the_answer_key(
    verify: ModuleType, tmp_path: Path
) -> None:
    out, key = write_smoke_run(tmp_path)

    problems = verify.smoke_problems(out, key)

    assert problems == []


@pytest.mark.parametrize(
    ("tamper", "problem"),
    [
        (
            lambda out: rewrite_summary(
                out,
                "payments",
                {"received": 3, "accepted": 3, "rejected": 0, "duplicates": 0},
            ),
            "payments are",
        ),
        (
            lambda out: rewrite_summary(
                out, "cases", {"created": 2, "changed": 0, "unchanged": 0}
            ),
            "cases are",
        ),
        (
            lambda out: (out / "ACC-1_20261103T000000Z.json").unlink(),
            "packs are",
        ),
        (
            lambda out: (out / "ACC-2_20261103T000000Z.md").write_text(
                "", encoding="utf-8"
            ),
            "packs are",
        ),
        (
            lambda out: (out / "run_summary.json").unlink(),
            "run_summary.json is missing",
        ),
    ],
    ids=[
        "payment counts off the key",
        "a case too many",
        "a case without its JSON pack",
        "a pack for a NO_ALERT account",
        "no summary",
    ],
)
def test_flags_a_smoke_run_that_doesnt_match_the_answer_key(
    verify: ModuleType,
    tmp_path: Path,
    tamper: Callable[[Path], object],
    problem: str,
) -> None:
    out, key = write_smoke_run(tmp_path)
    tamper(out)

    problems = verify.smoke_problems(out, key)

    assert len(problems) == 1
    assert problem in problems[0]


def test_flags_a_dot_env_file_that_git_would_track(
    verify: ModuleType, tmp_path: Path
) -> None:
    (tmp_path / ".env").write_text("X=1\n", encoding="utf-8")

    findings = verify.secrets_in(tmp_path, ".env")

    assert findings == [".env: a .env file isn't ignored"]


def write_tree(root: Path, names: list[str]) -> None:
    for name in names:
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("nothing secret\n", encoding="utf-8")


def test_lists_every_file_the_gitignore_doesnt_ignore_without_git(
    verify: ModuleType, tmp_path: Path
) -> None:
    (tmp_path / ".gitignore").write_text(
        ".venv/\n*.py[cod]\n.env\n.env.*\n!.env.example\n/build/\n", encoding="utf-8"
    )
    write_tree(
        tmp_path,
        [
            "README.md",
            "src/app.py",
            "src/app.pyc",
            ".venv/lib/site.py",
            ".env",
            ".env.local",
            ".env.example",
            "build/out.txt",
            "docs/build/kept.txt",
            ".git/config",
        ],
    )

    files = verify.walked_files(tmp_path)

    assert sorted(files) == [
        ".env.example",
        ".gitignore",
        "README.md",
        "docs/build/kept.txt",
        "src/app.py",
    ]


def test_the_secrets_gate_scans_a_folder_that_isnt_a_git_checkout(
    verify: ModuleType, tmp_path: Path
) -> None:
    (tmp_path / ".gitignore").write_text(".venv/\n", encoding="utf-8")
    write_tree(tmp_path, ["README.md", ".venv/lib/site.py"])
    (tmp_path / "config.txt").write_text(
        f'{KEY} = "hunter2hunter2"\n', encoding="utf-8"
    )
    (tmp_path / ".venv" / "lib" / "site.py").write_text(
        f'{KEY} = "hunter2hunter2"\n', encoding="utf-8"
    )

    outcome = verify.check_secrets(tmp_path)

    assert outcome.status == verify.Status.FAIL
    assert outcome.detail == "config.txt:1: looks like a hard-coded credential"


def test_the_secrets_gate_passes_a_clean_folder_that_isnt_a_git_checkout(
    verify: ModuleType, tmp_path: Path
) -> None:
    write_tree(tmp_path, ["README.md", "src/app.py"])

    outcome = verify.check_secrets(tmp_path)

    assert outcome.status == verify.Status.PASS
    assert (
        outcome.detail == "2 files checked (no Git checkout: .gitignore read directly)"
    )
