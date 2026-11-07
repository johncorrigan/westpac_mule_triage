from pathlib import Path

import pytest

from mule_triage.contracts import DEFAULT_MODEL, AccountId, Alert
from mule_triage.errors import RecordingMissing
from mule_triage.evidence import EvidencePack, facts_sha256
from mule_triage.narrative.client import RecordingKey, read_recording
from mule_triage.narrative.prompt import PROMPT_VERSION

RECORDED = Path(__file__).resolve().parents[1] / "ai" / "recorded"


@pytest.mark.xfail(strict=True, raises=RecordingMissing)
def test_every_golden_alert_has_a_recording_for_this_prompt_version_and_model(
    golden_packs: dict[AccountId, EvidencePack],
) -> None:
    alerts = [pack for pack in golden_packs.values() if isinstance(pack.result, Alert)]
    keys = [
        RecordingKey(DEFAULT_MODEL, PROMPT_VERSION, facts_sha256(pack))
        for pack in alerts
    ]

    recordings = [read_recording(RECORDED, key) for key in keys]

    assert len(recordings) == 2
