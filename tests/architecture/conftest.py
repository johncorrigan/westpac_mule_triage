import ast
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[2] / "src"


@pytest.fixture(scope="session")
def source_trees() -> dict[str, ast.Module]:
    trees: dict[str, ast.Module] = {}
    for path in sorted(SRC.rglob("*.py")):
        name = ".".join(path.relative_to(SRC).with_suffix("").parts)
        trees[name] = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    return trees
