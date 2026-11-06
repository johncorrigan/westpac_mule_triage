import hashlib
import importlib.util
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


def test_ignores_an_unlisted_file_beside_the_answer_key(
    verify: ModuleType, tmp_path: Path
) -> None:
    key = write_answer_key(tmp_path / "golden")
    (tmp_path / "golden" / "test_golden.py").write_bytes(b"")

    outcome = verify.check_golden(key)

    assert outcome.status == verify.Status.PASS


def test_skips_while_there_is_no_answer_key(verify: ModuleType, tmp_path: Path) -> None:
    outcome = verify.check_golden(tmp_path / "golden" / "expected")

    assert outcome.status == verify.Status.SKIP


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


def test_flags_a_dot_env_file_that_git_would_track(
    verify: ModuleType, tmp_path: Path
) -> None:
    (tmp_path / ".env").write_text("X=1\n", encoding="utf-8")

    findings = verify.secrets_in(tmp_path, ".env")

    assert findings == [".env: a .env file isn't ignored"]
