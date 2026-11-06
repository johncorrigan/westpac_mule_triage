import pytest

from mule_triage.cli import ExitCode, main


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
    }
