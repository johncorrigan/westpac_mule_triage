"""Each evidence pack as Markdown and JSON.

In Markdown, every ID and every reference is inline code, so text from the data can't render
as a link, an image, HTML or a heading. Lists rather than tables, because a pipe in a reference
would split a table cell even inside inline code.
"""

import json
from collections.abc import Iterable
from datetime import UTC
from pathlib import Path

from mule_triage.contracts import Alert, NarrativeOutcome
from mule_triage.evidence import (
    EvidencePack,
    JsonObject,
    PaymentFact,
    ProfileFact,
    SignalFact,
    dollars,
    fact_document,
    facts_sha256,
    utc_iso,
)
from mule_triage.decision.review import CaseView, StoredNarrative


def inline_code(text: str) -> str:
    """One code span of ASCII, whatever backticks or control characters the text holds."""
    escaped = "".join(
        char if char.isprintable() and char.isascii() else json.dumps(char)[1:-1]
        for char in text
    )
    longest = run = 0
    for char in escaped:
        run = run + 1 if char == "`" else 0
        longest = max(longest, run)
    fence = "`" * (longest + 1)
    padded = escaped[:1] in ("`", " ") or escaped[-1:] in ("`", " ")
    pad = " " if padded else ""
    return f"{fence}{pad}{escaped}{pad}{fence}"


def quoted(text: str) -> str:
    return inline_code(json.dumps(text, ensure_ascii=True))


def report_name(pack: EvidencePack) -> str:
    return f"{pack.account_id}_{pack.as_of.astimezone(UTC):%Y%m%dT%H%M%SZ}"


def headline(pack: EvidencePack) -> str:
    result = pack.result
    if isinstance(result, Alert):
        return f"{result.outcome}, priority {result.priority}"
    return result.outcome.value


def incomplete(pack: EvidencePack) -> bool:
    return isinstance(pack.result, Alert) and pack.result.incomplete


def provenance(fact: SignalFact | PaymentFact | ProfileFact) -> str:
    return (
        f"{inline_code(fact.fact_id)}: source {inline_code(fact.source)},"
        f" record {inline_code(fact.record_id)}, event {utc_iso(fact.event_time)},"
        f" known {utc_iso(fact.knowledge_time)}"
    )


def pack_markdown(pack: EvidencePack, version: int) -> str:
    reasons = ", ".join(sorted(pack.result.reasons)) or "none"
    lines = [
        f"# Case {inline_code(pack.account_id)} at {utc_iso(pack.as_of)}",
        "",
        f"- Case version: {version}",
        f"- Outcome: {headline(pack)}",
        f"- INCOMPLETE: {'yes' if incomplete(pack) else 'no'}",
        f"- Reasons: {reasons}",
        f"- Rules version: {inline_code(pack.rules_version)}",
        f"- Facts SHA-256: {inline_code(facts_sha256(pack))}",
        "",
        "## Missing data",
        "",
    ]
    lines += [
        f"- {m.signal} is unknown ({m.reason}). {m.what} {m.why_it_matters}"
        for m in pack.missing
    ] or ["- None."]
    lines += ["", "## Signals", ""]
    for signal in pack.signals:
        measures = "; ".join(
            f"{m.name} {m.value}, threshold {m.threshold}" for m in signal.measures
        )
        lines.append(f"- {provenance(signal)}. {signal.status}: {measures}.")
    lines += ["", "## Payments", ""]
    lines += [
        f"- {provenance(p)}. From {inline_code(p.from_account)} to"
        f" {inline_code(p.to_account)}, ${dollars(p.amount_cents)}."
        f" Reference (untrusted data): {quoted(p.reference)}"
        for p in pack.payments
    ] or ["- None in the window."]
    lines += ["", "## Profile", ""]
    profile = pack.profile
    lines.append(
        "- None known by the as-of time."
        if profile is None
        else f"- {provenance(profile)}. Opened {utc_iso(profile.event_time)}."
    )
    lines += ["", "## Counterparties", "", pack.counterparties.note, ""]
    lines += [
        f"- {inline_code(c.account_id)}: ${dollars(c.total_cents)} in total, from"
        f" {', '.join(inline_code(fact_id) for fact_id in c.fact_ids)}"
        for c in pack.counterparties.shown
    ]
    return "\n".join(lines) + "\n"


def case_markdown(view: CaseView) -> str:
    """The stored case, its pack rebuilt from the stored records, and its audit history."""
    case = view.case
    lines = [f"Case status: {case.status}, version {case.version}"]
    if not view.matches_case:
        lines.append(
            "Warning: with these rules the pack below differs from the stored case;"
            " use the run's rules file before disposing."
        )
    lines += ["", pack_markdown(view.pack, case.version)]
    lines += narrative_markdown(view.narrative)
    lines += ["## Audit history", ""]
    lines += [
        f"- {utc_iso(event.occurred_at)} {event.event_type}: {inline_code(event.payload)}"
        for event in view.history
    ]
    return "\n".join(lines) + "\n"


def narrative_markdown(narrative: StoredNarrative | None) -> list[str]:
    """Every text from the narrative event is inline code: a draft's sentences are the
    model's words, which the validator checks for facts, not for Markdown."""
    lines = ["## Narrative", ""]
    if narrative is None:
        return [*lines, "- There's no narrative for this version.", ""]
    if narrative.outcome is NarrativeOutcome.AI_DRAFT:
        lines.append(
            f"- Source: an AI draft ({narrative.source}), from model"
            f" {inline_code(narrative.model or 'unknown')}, digest"
            f" {inline_code(narrative.digest or 'unknown')}"
        )
    else:
        lines.append(f"- Source: the template ({narrative.outcome})")
        lines.append(f"- Note: {inline_code(narrative.note or 'none')}")
        lines += [f"- Reason: {inline_code(reason)}" for reason in narrative.reasons]
    lines += ["", "Sentences, each with the facts it cites:", ""]
    lines += [
        f"- {inline_code(sentence.text)}, citing"
        f" {', '.join(inline_code(fact_id) for fact_id in sentence.fact_ids)}"
        for sentence in narrative.sentences
    ]
    return [*lines, ""]


def pack_document(pack: EvidencePack, version: int) -> JsonObject:
    result = pack.result
    return {
        "account_id": pack.account_id,
        "as_of": utc_iso(pack.as_of),
        "case_version": version,
        "rules_version": pack.rules_version,
        "outcome": result.outcome.value,
        "priority": result.priority.value if isinstance(result, Alert) else None,
        "incomplete": incomplete(pack),
        "reasons": sorted(result.reasons),
        "missing_data": [
            {
                "signal": m.signal.value,
                "reason": m.reason.value,
                "what": m.what,
                "why_it_matters": m.why_it_matters,
            }
            for m in pack.missing
        ],
        "facts_sha256": facts_sha256(pack),
        "facts": [fact_document(fact) for fact in pack.facts],
        "counterparties": {
            "note": pack.counterparties.note,
            "total": pack.counterparties.total,
            "shown": [
                {
                    "account_id": c.account_id,
                    "total_dollars": dollars(c.total_cents),
                    "fact_ids": list(c.fact_ids),
                }
                for c in pack.counterparties.shown
            ],
        },
    }


def pack_json(pack: EvidencePack, version: int) -> str:
    return json.dumps(pack_document(pack, version), indent=2, ensure_ascii=True) + "\n"


def write_reports(
    folder: Path, packs: Iterable[tuple[EvidencePack, int]]
) -> tuple[Path, ...]:
    """Each case's pack, given with its version, as `<account>_<as-of>.md` and `.json`."""
    folder.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for pack, version in packs:
        for suffix, text in (
            (".md", pack_markdown(pack, version)),
            (".json", pack_json(pack, version)),
        ):
            path = folder / f"{report_name(pack)}{suffix}"
            path.write_text(text, encoding="utf-8", newline="\n")
            written.append(path)
    return tuple(written)
