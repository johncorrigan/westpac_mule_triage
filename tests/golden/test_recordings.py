from collections import Counter
from pathlib import Path

from mule_triage.contracts import DEFAULT_MODEL, AccountId, Alert
from mule_triage.evidence import EvidencePack, facts_sha256
from mule_triage.narrative.client import RecordingKey, read_recording
from mule_triage.narrative.prompt import PROMPT_VERSION
from mule_triage.narrative.validator import Rejected, validate

RECORDED = Path(__file__).resolve().parents[1] / "ai" / "recorded"


def golden_alerts(packs: dict[AccountId, EvidencePack]) -> list[EvidencePack]:
    return [pack for pack in packs.values() if isinstance(pack.result, Alert)]


def recording_key(pack: EvidencePack) -> RecordingKey:
    return RecordingKey(DEFAULT_MODEL, PROMPT_VERSION, facts_sha256(pack))


def test_every_golden_alert_has_a_recording_for_this_prompt_version_and_model(
    golden_packs: dict[AccountId, EvidencePack],
) -> None:
    keys = [recording_key(pack) for pack in golden_alerts(golden_packs)]

    recordings = [read_recording(RECORDED, key) for key in keys]

    assert len(recordings) == 2


def test_the_recorded_drafts_replay_with_the_verdicts_verify_md_reports(
    golden_packs: dict[AccountId, EvidencePack],
) -> None:
    """`evidence/verify.md` reports these, so a new recording or a changed validator must
    update it."""
    alerts = golden_alerts(golden_packs)

    verdicts = {
        pack.account_id: validate(
            read_recording(RECORDED, recording_key(pack)).reply, pack
        )
        for pack in alerts
    }

    reasons = {
        account: Counter(problem.reason.value for problem in verdict.problems)
        for account, verdict in verdicts.items()
        if isinstance(verdict, Rejected)
    }
    assert reasons == {
        "ACC-101": {"UNSUPPORTED_DATE": 5, "UNSUPPORTED_NUMBER": 2},
        "ACC-106": {"UNSUPPORTED_DATE": 5, "UNSUPPORTED_NUMBER": 2},
    }
