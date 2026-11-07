import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType

import pytest

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"


@pytest.fixture(scope="module")
def quickstart() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "quickstart", SCRIPTS / "quickstart.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["quickstart"] = module
    spec.loader.exec_module(module)
    return module


def test_a_second_demo_starts_from_an_empty_folder_and_shows_the_same_summary(
    quickstart: ModuleType, tmp_path: Path, capfd: pytest.CaptureFixture[str]
) -> None:
    folder = tmp_path / "demo"
    assert quickstart.demo(sys.executable, folder)
    first = (folder / "packs" / "run_summary.json").read_bytes()
    capfd.readouterr()

    shown = quickstart.demo(sys.executable, folder)

    assert shown
    assert (folder / "packs" / "run_summary.json").read_bytes() == first
    assert json.loads(first)["run_id"] == 1
    output = capfd.readouterr().out
    assert "Case status: OPEN, version 1" in output
    assert f"evidence packs: {folder / 'packs'}" in output
