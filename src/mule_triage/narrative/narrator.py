"""One case's narrative: the model's checked draft, or the template with the reason there's none.

A NEEDS_REVIEW case always gets the template, and the model is never asked. A NO_ALERT case
gets no narrative.
"""

from dataclasses import dataclass
from typing import TypeAlias, assert_never

from mule_triage.contracts import Alert, NarrativeOutcome, NeedsReview, NoAlert
from mule_triage.evidence import EvidencePack, facts_sha256
from mule_triage.narrative.client import (
    Drafted,
    ModelClient,
    ModelLabel,
    NotDrafted,
)
from mule_triage.narrative.prompt import PROMPT_VERSION, build_prompt
from mule_triage.narrative.template import (
    TemplateNarrative,
    for_needs_review,
    for_no_draft,
    for_rejected_draft,
)
from mule_triage.narrative.validator import (
    Accepted,
    Rejected,
    Sentence,
    Verdict,
    validate,
)


@dataclass(frozen=True, slots=True)
class AiDraft:
    sentences: tuple[Sentence, ...]
    label: ModelLabel


Narrative: TypeAlias = AiDraft | TemplateNarrative


@dataclass(frozen=True, slots=True)
class NarrativeRecord:
    """The narrative, and what the audit keeps about how it was made."""

    pack: EvidencePack
    outcome: NarrativeOutcome
    narrative: Narrative
    prompt_version: str
    facts_sha256: str
    label: ModelLabel | None
    reply: str | None
    verdict: Verdict | None
    latency_seconds: float | None


def narrate(pack: EvidencePack, client: ModelClient) -> NarrativeRecord:
    match pack.result:
        case NeedsReview():
            return template_record(
                pack, NarrativeOutcome.NEEDS_REVIEW, for_needs_review(pack)
            )
        case NoAlert():
            raise ValueError(f"{pack.account_id} is NO_ALERT, which gets no narrative")
        case Alert():
            pass
        case _:
            assert_never(pack.result)
    drafted = client.draft(build_prompt(pack), facts_sha256(pack))
    match drafted:
        case NotDrafted(outcome, detail):
            reason = f"{outcome}: {detail}"
            return template_record(pack, outcome, for_no_draft(pack, reason))
        case Drafted(reply, label, latency):
            verdict = validate(reply, pack)
            return NarrativeRecord(
                pack=pack,
                outcome=outcome_of(verdict),
                narrative=drafted_narrative(pack, verdict, label),
                prompt_version=PROMPT_VERSION,
                facts_sha256=facts_sha256(pack),
                label=label,
                reply=reply,
                verdict=verdict,
                latency_seconds=latency,
            )
        case _:
            assert_never(drafted)


def template_record(
    pack: EvidencePack, outcome: NarrativeOutcome, narrative: TemplateNarrative
) -> NarrativeRecord:
    return NarrativeRecord(
        pack=pack,
        outcome=outcome,
        narrative=narrative,
        prompt_version=PROMPT_VERSION,
        facts_sha256=facts_sha256(pack),
        label=None,
        reply=None,
        verdict=None,
        latency_seconds=None,
    )


def outcome_of(verdict: Verdict) -> NarrativeOutcome:
    match verdict:
        case Accepted():
            return NarrativeOutcome.AI_DRAFT
        case Rejected():
            return NarrativeOutcome.REJECTED
        case _:
            assert_never(verdict)


def drafted_narrative(
    pack: EvidencePack, verdict: Verdict, label: ModelLabel
) -> Narrative:
    match verdict:
        case Accepted(sentences):
            return AiDraft(sentences, label)
        case Rejected():
            return for_rejected_draft(pack, verdict)
        case _:
            assert_never(verdict)
