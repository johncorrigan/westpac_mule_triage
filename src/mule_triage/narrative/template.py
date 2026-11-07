"""The template narrative: sentences built from the pack's facts alone, each citing its facts.

The reviewer gets it whenever there's no AI draft, with a note to write the narrative by hand
and the reasons: when a draft is rejected, and always for a NEEDS_REVIEW case, which a person
decides. It never repeats a payment's reference; the pack shows those as quoted data.
"""

from dataclasses import dataclass
from typing import assert_never

from mule_triage.contracts import AccountResult, Alert, NeedsReview, NoAlert
from mule_triage.evidence import (
    EvidencePack,
    MissingData,
    PaymentFact,
    SignalFact,
    dollars,
    utc_iso,
)
from mule_triage.narrative.validator import Rejected, Sentence

NO_AI_DRAFT = "no AI draft: write this one manually"


@dataclass(frozen=True, slots=True)
class TemplateNarrative:
    note: str
    reasons: tuple[str, ...]
    sentences: tuple[Sentence, ...]


def for_rejected_draft(pack: EvidencePack, rejected: Rejected) -> TemplateNarrative:
    reasons = tuple(problem.text for problem in rejected.problems)
    return TemplateNarrative(NO_AI_DRAFT, reasons, template_sentences(pack))


def for_no_draft(pack: EvidencePack, reason: str) -> TemplateNarrative:
    return TemplateNarrative(NO_AI_DRAFT, (reason,), template_sentences(pack))


def for_needs_review(pack: EvidencePack) -> TemplateNarrative:
    missing = "; ".join(f"{m.signal} is unknown ({m.reason})" for m in pack.missing)
    reason = (
        "NEEDS_REVIEW: a person decides this case, so it gets no AI draft;"
        f" missing: {missing or 'nothing recorded'}"
    )
    return TemplateNarrative(NO_AI_DRAFT, (reason,), template_sentences(pack))


def template_sentences(pack: EvidencePack) -> tuple[Sentence, ...]:
    signal_ids = {signal.signal: signal.fact_id for signal in pack.signals}
    sentences = [
        Sentence(
            f"{pack.account_id} is {outcome(pack.result)}, under rules"
            f" {pack.rules_version}.",
            tuple(signal_ids.values()),
        ),
        *(signal_sentence(signal) for signal in pack.signals),
        *(missing_sentence(m, signal_ids[m.signal]) for m in pack.missing),
    ]
    received = [p for p in pack.payments if p.to_account == pack.account_id]
    sent = [p for p in pack.payments if p.from_account == pack.account_id]
    for label, payments in (("in", received), ("out", sent)):
        if payments:
            sentences.append(payments_sentence(label, payments))
    if pack.profile is not None:
        opened = utc_iso(pack.profile.event_time)
        sentences.append(
            Sentence(f"The account was opened at {opened}.", (pack.profile.fact_id,))
        )
    return tuple(sentences)


def outcome(result: AccountResult) -> str:
    match result:
        case Alert():
            flag = ", INCOMPLETE" if result.incomplete else ""
            return f"{result.outcome}, priority {result.priority}{flag}"
        case NeedsReview() | NoAlert():
            return result.outcome.value
        case _:
            assert_never(result)


def signal_sentence(signal: SignalFact) -> Sentence:
    measures = "; ".join(
        f"{m.name} {m.value}, threshold {m.threshold}" for m in signal.measures
    )
    return Sentence(
        f"{signal.signal} is {signal.status}: {measures}.", (signal.fact_id,)
    )


def missing_sentence(missing: MissingData, fact_id: str) -> Sentence:
    return Sentence(
        f"{missing.signal} is unknown ({missing.reason}). {missing.what}"
        f" {missing.why_it_matters}",
        (fact_id,),
    )


def payments_sentence(label: str, payments: list[PaymentFact]) -> Sentence:
    total = dollars(sum(p.amount_cents for p in payments))
    return Sentence(
        f"Payments {label}: {len(payments)}, ${total} in total, the first at"
        f" {utc_iso(payments[0].event_time)} and the last at"
        f" {utc_iso(payments[-1].event_time)}.",
        tuple(p.fact_id for p in payments),
    )
