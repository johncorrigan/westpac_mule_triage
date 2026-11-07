from datetime import UTC, datetime
from pathlib import Path

import pytest

from mule_triage.cli import load_rules
from mule_triage.contracts import AccountId, RulesConfig
from mule_triage.evidence import EvidencePack, build_pack
from mule_triage.ingest import parse_payments, parse_profiles, read_lines
from mule_triage.signals import activities

ROOT = Path(__file__).resolve().parents[1]
RULES_V1 = ROOT / "rules" / "rules-v1.toml"
GOLDEN = ROOT / "tests" / "golden"
GOLDEN_AS_OF = datetime(2026, 11, 3, tzinfo=UTC)


@pytest.fixture
def rules() -> RulesConfig:
    return load_rules(RULES_V1)


@pytest.fixture
def golden_packs(rules: RulesConfig) -> dict[AccountId, EvidencePack]:
    """Every golden account's pack, with the duplicate payment stored once, as the store does."""
    parsed = parse_payments(read_lines(GOLDEN / "payments.jsonl")).accepted
    payments = {each.record.transaction_id: each.record for each in parsed}
    profiles = [
        each.record
        for each in parse_profiles(read_lines(GOLDEN / "profiles.jsonl")).accepted
    ]
    return {
        activity.account_id: build_pack(activity, rules)
        for activity in activities(payments.values(), profiles, GOLDEN_AS_OF, rules)
    }


@pytest.fixture
def acc_101(golden_packs: dict[AccountId, EvidencePack]) -> EvidencePack:
    return golden_packs[AccountId("ACC-101")]
