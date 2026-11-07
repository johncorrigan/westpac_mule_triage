import json

import pytest

from mule_triage.contracts import AccountId
from mule_triage.evidence import EvidencePack
from mule_triage.narrative.template import (
    NO_AI_DRAFT,
    for_needs_review,
    for_rejected_draft,
    template_sentences,
)
from mule_triage.narrative.validator import Rejected, validate


def test_a_rejected_draft_gives_the_template_with_the_note_and_the_reason(
    acc_101: EvidencePack,
) -> None:
    draft = json.dumps(
        {"sentences": [{"text": "ACC-101 is legitimate.", "fact_ids": ["S-FAN_IN"]}]}
    )
    verdict = validate(draft, acc_101)
    assert isinstance(verdict, Rejected)

    narrative = for_rejected_draft(acc_101, verdict)

    assert narrative.note == NO_AI_DRAFT == "no AI draft: write this one manually"
    assert narrative.reasons == (
        "DECISION_LANGUAGE: sentence 1 uses decision language: legitimate",
    )
    assert narrative.sentences == template_sentences(acc_101)


def test_the_template_states_the_outcome_and_each_signal_citing_its_facts(
    acc_101: EvidencePack,
) -> None:
    sentences = template_sentences(acc_101)

    assert [(s.text, s.fact_ids) for s in sentences[:2]] == [
        (
            "ACC-101 is ALERT, priority HIGH, under rules v1.",
            ("S-FAN_IN", "S-PASS_THROUGH", "S-NEW_ACCOUNT"),
        ),
        (
            "FAN_IN is BREACHED: distinct senders 7, threshold at least 5.",
            ("S-FAN_IN",),
        ),
    ]


@pytest.mark.parametrize("account", ["ACC-101", "ACC-105", "ACC-106", "ACC-107"])
def test_every_template_sentence_cites_facts_in_its_pack(
    golden_packs: dict[AccountId, EvidencePack], account: str
) -> None:
    pack = golden_packs[AccountId(account)]
    known = {fact.fact_id for fact in pack.facts}

    sentences = template_sentences(pack)

    assert sentences
    assert all(s.fact_ids and set(s.fact_ids) <= known for s in sentences)


def test_the_template_never_carries_a_payment_reference(
    acc_101: EvidencePack,
) -> None:
    references = {payment.reference for payment in acc_101.payments}

    text = " ".join(s.text for s in template_sentences(acc_101))

    assert [r for r in references if r in text] == []


def test_a_needs_review_case_gets_the_template_saying_what_is_missing(
    golden_packs: dict[AccountId, EvidencePack],
) -> None:
    pack = golden_packs[AccountId("ACC-105")]
    (missing,) = pack.missing

    narrative = for_needs_review(pack)

    assert narrative.note == NO_AI_DRAFT
    assert narrative.reasons == (
        "NEEDS_REVIEW: a person decides this case, so it gets no AI draft;"
        " missing: NEW_ACCOUNT is unknown (PROFILE_MISSING)",
    )
    (said,) = [s for s in narrative.sentences if missing.what in s.text]
    assert said.fact_ids == ("S-NEW_ACCOUNT",)
