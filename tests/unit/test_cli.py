import pytest

from mule_triage.cli import main


def test_prints_usage_and_succeeds_when_given_no_command(
    capsys: pytest.CaptureFixture[str],
) -> None:
    exit_code = main([])

    assert exit_code == 0
    assert capsys.readouterr().out.startswith("usage: mule_triage")
