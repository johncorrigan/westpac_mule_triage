from pathlib import Path

import pytest

from mule_triage.cli import load_rules
from mule_triage.contracts import RulesConfig

RULES_V1 = Path(__file__).resolve().parents[1] / "rules" / "rules-v1.toml"


@pytest.fixture
def rules() -> RulesConfig:
    return load_rules(RULES_V1)
