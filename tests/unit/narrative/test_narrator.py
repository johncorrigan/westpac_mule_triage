import json
from dataclasses import dataclass, field

import pytest

from mule_triage.contracts import AccountId, DEFAULT_MODEL, NarrativeOutcome
from mule_triage.evidence import EvidencePack, facts_sha256
from mule_triage.narrative.client import (
    DraftResult,
    DraftSource,
    Drafted,
    ModelLabel,
    NotDrafted,
)
from mule_triage.narrative.narrator import AiDraft, narrate
from mule_triage.narrative.prompt import PROMPT_VERSION, Prompt
from mule_triage.narrative.template import NO_AI_DRAFT, TemplateNarrative
from mule_triage.narrative.validator import Accepted, Rejected

LABEL = ModelLabel(DEFAULT_MODEL, "sha256:5f2a9c", DraftSource.REPLAY)
GOOD = json.dumps(
    {
        "sentences": [
            {
                "text": "ACC-101 was paid by 7 distinct senders, at least 5 needed.",
                "fact_ids": ["S-FAN_IN"],
            }
        ]
    }
)


@dataclass
class FakeClient:
    result: DraftResult
    prompts: list[Prompt] = field(default_factory=list)

    def draft(self, prompt: Prompt, facts_sha256: str) -> DraftResult:
        self.prompts.append(prompt)
        return self.result


def test_an_accepted_draft_is_the_narrative_with_its_label_reply_and_verdict(
    acc_101: EvidencePack,
) -> None:
    client = FakeClient(Drafted(GOOD, LABEL, None))

    record = narrate(acc_101, client)

    assert record.outcome is NarrativeOutcome.AI_DRAFT
    assert isinstance(record.narrative, AiDraft)
    assert record.narrative.label == LABEL
    assert (record.reply, record.label) == (GOOD, LABEL)
    assert isinstance(record.verdict, Accepted)
    assert (record.prompt_version, record.facts_sha256) == (
        PROMPT_VERSION,
        facts_sha256(acc_101),
    )


def test_a_rejected_draft_gives_the_template_with_the_validators_reasons(
    acc_101: EvidencePack,
) -> None:
    client = FakeClient(Drafted("not JSON", LABEL, None))

    record = narrate(acc_101, client)

    assert record.outcome is NarrativeOutcome.REJECTED
    assert isinstance(record.narrative, TemplateNarrative)
    assert record.narrative.reasons == (
        "MALFORMED: the reply isn't JSON in the requested shape",
    )
    assert isinstance(record.verdict, Rejected)
    assert record.reply == "not JSON"


@pytest.mark.parametrize(
    "outcome",
    [
        NarrativeOutcome.AI_OFF,
        NarrativeOutcome.NO_RECORDING,
        NarrativeOutcome.AI_UNAVAILABLE,
    ],
)
def test_no_draft_gives_the_template_with_the_note_and_the_reason(
    acc_101: EvidencePack, outcome: NarrativeOutcome
) -> None:
    client = FakeClient(NotDrafted(outcome, "the detail"))

    record = narrate(acc_101, client)

    assert record.outcome is outcome
    assert isinstance(record.narrative, TemplateNarrative)
    assert record.narrative.note == NO_AI_DRAFT
    assert record.narrative.reasons == (f"{outcome}: the detail",)
    assert (record.reply, record.verdict, record.label) == (None, None, None)


def test_a_needs_review_case_gets_the_template_and_the_model_is_never_asked(
    golden_packs: dict[AccountId, EvidencePack],
) -> None:
    client = FakeClient(Drafted(GOOD, LABEL, None))

    record = narrate(golden_packs[AccountId("ACC-105")], client)

    assert record.outcome is NarrativeOutcome.NEEDS_REVIEW
    assert isinstance(record.narrative, TemplateNarrative)
    assert client.prompts == []


def test_a_no_alert_case_gets_no_narrative(
    golden_packs: dict[AccountId, EvidencePack],
) -> None:
    client = FakeClient(Drafted(GOOD, LABEL, None))

    with pytest.raises(ValueError, match="NO_ALERT"):
        narrate(golden_packs[AccountId("ACC-107")], client)
