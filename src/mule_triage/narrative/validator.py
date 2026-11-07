"""The narrative validator: a pure check of a model's reply against this case's pack.

A reply passes only if it's JSON of the requested shape within the prompt's limits, every
sentence cites at least one fact and only facts in the pack, every ID, number, amount and date
in a sentence appears in the facts it cites, and no sentence uses decision or recommendation
language or repeats a payment's reference. Text is normalised first, so look-alike and
invisible characters can't hide a word. Numbers compare by value, so $900 matches 900.00,
after the pack's IDs are taken out; the case's own account may always be named. Dates and
times must match a cited fact's exactly. A payment's reference never supports a number,
because the payer wrote it. A problem's detail never quotes the reply beyond a number, a date,
a listed term or an ID from the pack.
"""

import re
import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from typing import TypeAlias, assert_never

from pydantic import BaseModel, ConfigDict, ValidationError

from mule_triage.evidence import (
    EvidencePack,
    Fact,
    PaymentFact,
    ProfileFact,
    SignalFact,
    dollars,
    utc_iso,
)
from mule_triage.narrative.prompt import MAX_SENTENCE_CHARACTERS, MAX_SENTENCES

DECISION_TERMS = (
    "block",
    "blocks",
    "blocked",
    "blocking",
    "close",
    "closes",
    "closed",
    "closing",
    "closure",
    "freeze",
    "freezes",
    "froze",
    "frozen",
    "suspend",
    "suspended",
    "report",
    "reports",
    "reported",
    "reporting",
    "escalate",
    "escalated",
    "escalation",
    "recommend",
    "recommends",
    "recommended",
    "recommendation",
    "should",
    "must",
    "legitimate",
    "illegitimate",
    "innocent",
    "guilty",
    "suspicious",
    "fraud",
    "fraudulent",
    "mule",
    "no further action",
    "false positive",
    "insufficient evidence",
)
DECISION_LANGUAGE = re.compile(
    r"\b(?:" + "|".join(term.replace(" ", r"\s+") for term in DECISION_TERMS) + r")\b",
    re.IGNORECASE,
)
DATE_OR_TIME = re.compile(
    r"\d{4}-\d{1,2}-\d{1,2}(?:T\d{1,2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:Z|[+-]\d{2}:\d{2})?)?"
    r"|\d{1,2}:\d{2}(?::\d{2})?"
)
NUMBER = re.compile(r"\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?")
ID_EDGE = "A-Za-z0-9_-"
HAS_LETTER = re.compile(r"[A-Za-z]")
WORD = re.compile(r"[^\W_]+")
# Three words in a row from a reference count as repeating it; one or two, such as "rent"
# or "car deposit", are ordinary words a narrative may use.
REPEAT_WORDS = 3


class Reason(StrEnum):
    MALFORMED = "MALFORMED"
    OUT_OF_LIMITS = "OUT_OF_LIMITS"
    UNCITED_SENTENCE = "UNCITED_SENTENCE"
    UNKNOWN_FACT = "UNKNOWN_FACT"
    UNSUPPORTED_ID = "UNSUPPORTED_ID"
    UNSUPPORTED_NUMBER = "UNSUPPORTED_NUMBER"
    UNSUPPORTED_DATE = "UNSUPPORTED_DATE"
    DECISION_LANGUAGE = "DECISION_LANGUAGE"
    REPEATS_REFERENCE = "REPEATS_REFERENCE"


class ReplySentence(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    text: str
    fact_ids: tuple[str, ...]


class Reply(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    sentences: tuple[ReplySentence, ...]


@dataclass(frozen=True, slots=True)
class Sentence:
    text: str
    fact_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class Problem:
    reason: Reason
    detail: str

    @property
    def text(self) -> str:
        return f"{self.reason}: {self.detail}"


@dataclass(frozen=True, slots=True)
class Accepted:
    sentences: tuple[Sentence, ...]


@dataclass(frozen=True, slots=True)
class Rejected:
    problems: tuple[Problem, ...]


Verdict: TypeAlias = Accepted | Rejected


@dataclass(frozen=True, slots=True)
class PackIndex:
    """What each sentence is checked against, built once per reply."""

    subject: str
    facts: dict[str, Fact]
    ids: re.Pattern[str]
    reference_runs: frozenset[tuple[str, ...]]


def validate(reply: str, pack: EvidencePack) -> Verdict:
    try:
        parsed = Reply.model_validate_json(reply)
    except ValidationError:
        detail = "the reply isn't JSON in the requested shape"
        return Rejected((Problem(Reason.MALFORMED, detail),))
    sentences = tuple(
        Sentence(normalised(s.text), s.fact_ids) for s in parsed.sentences
    )
    index = PackIndex(
        subject=pack.account_id,
        facts={fact.fact_id: fact for fact in pack.facts},
        ids=id_pattern(pack),
        reference_runs=frozenset(
            run for payment in pack.payments for run in word_runs(payment.reference)
        ),
    )
    problems = limit_problems(sentences)
    for number, sentence in enumerate(sentences, start=1):
        problems.extend(sentence_problems(number, sentence, index))
    return Rejected(tuple(problems)) if problems else Accepted(sentences)


def normalised(text: str) -> str:
    """Look-alike characters as plain ones, and invisible ones removed, so neither can hide a
    word or a number from the checks. An accepted draft keeps this text: what passed is what
    the reviewer reads."""
    folded = unicodedata.normalize("NFKC", text)
    return "".join(char for char in folded if unicodedata.category(char) != "Cf")


def limit_problems(sentences: tuple[Sentence, ...]) -> list[Problem]:
    problems: list[Problem] = []
    if not 1 <= len(sentences) <= MAX_SENTENCES:
        detail = f"{len(sentences)} sentences, not 1 to {MAX_SENTENCES}"
        problems.append(Problem(Reason.OUT_OF_LIMITS, detail))
    for number, sentence in enumerate(sentences, start=1):
        if not sentence.text.strip():
            problems.append(
                Problem(Reason.OUT_OF_LIMITS, f"sentence {number} is blank")
            )
        elif len(sentence.text) > MAX_SENTENCE_CHARACTERS:
            detail = (
                f"sentence {number} has {len(sentence.text)} characters,"
                f" over {MAX_SENTENCE_CHARACTERS}"
            )
            problems.append(Problem(Reason.OUT_OF_LIMITS, detail))
    return problems


def sentence_problems(
    number: int, sentence: Sentence, index: PackIndex
) -> list[Problem]:
    """A sentence whose citations fail isn't checked for IDs or values: it has nothing to
    check them against."""
    problems = [
        Problem(
            Reason.DECISION_LANGUAGE,
            f"sentence {number} uses decision language: {term}",
        )
        for term in decision_terms(sentence.text)
    ]
    if word_runs(sentence.text) & index.reference_runs:
        detail = f"sentence {number} repeats a payment's reference"
        problems.append(Problem(Reason.REPEATS_REFERENCE, detail))
    if not sentence.fact_ids:
        detail = f"sentence {number} cites no fact"
        return [Problem(Reason.UNCITED_SENTENCE, detail), *problems]
    if any(fact_id not in index.facts for fact_id in sentence.fact_ids):
        detail = f"sentence {number} cites a fact that isn't in this case's pack"
        return [Problem(Reason.UNKNOWN_FACT, detail), *problems]
    cited = [index.facts[fact_id] for fact_id in sentence.fact_ids]
    named = {index.subject, *(each for fact in cited for each in named_ids(fact))}
    problems.extend(
        Problem(
            Reason.UNSUPPORTED_ID,
            f"sentence {number}: {found} isn't named in the facts it cites",
        )
        for found in sorted(set(index.ids.findall(sentence.text)) - named)
    )
    numbers, dates = supported_values(cited)
    without_ids = index.ids.sub(" ", sentence.text)
    problems.extend(
        Problem(
            Reason.UNSUPPORTED_DATE,
            f"sentence {number}: {found} isn't a date or time in the facts it cites",
        )
        for found in DATE_OR_TIME.findall(without_ids)
        if found not in dates
    )
    problems.extend(
        Problem(
            Reason.UNSUPPORTED_NUMBER,
            f"sentence {number}: {found} isn't in the facts it cites",
        )
        for found in NUMBER.findall(DATE_OR_TIME.sub(" ", without_ids))
        if value(found) not in numbers
    )
    return problems


def decision_terms(text: str) -> list[str]:
    """An underscore counts as a break here, as in legitimate_activity, though IDs keep it."""
    words = text.replace("_", " ")
    found = {
        " ".join(m.group().lower().split()) for m in DECISION_LANGUAGE.finditer(words)
    }
    return sorted(found)


def word_runs(text: str) -> frozenset[tuple[str, ...]]:
    """Every run of REPEAT_WORDS words, ignoring case, spacing and punctuation."""
    words = WORD.findall(normalised(text).casefold())
    last = len(words) - REPEAT_WORDS + 1
    return frozenset(tuple(words[i : i + REPEAT_WORDS]) for i in range(last))


def id_pattern(pack: EvidencePack) -> re.Pattern[str]:
    """The pack's IDs as whole tokens, longest first, so ACC-101 is never read as 101.

    An ID with no letter stays a number to check, so an account named 950 can't hide $950.
    """
    ids: set[str] = {pack.account_id}
    for fact in pack.facts:
        ids.update((fact.fact_id, fact.record_id))
    for payment in pack.payments:
        ids.update((payment.from_account, payment.to_account))
    named = sorted((i for i in ids if HAS_LETTER.search(i)), key=len, reverse=True)
    alternatives = "|".join(re.escape(i) for i in named)
    return re.compile(rf"(?<![{ID_EDGE}])(?:{alternatives or '(?!)'})(?![{ID_EDGE}])")


def value(number: str) -> Decimal:
    return Decimal(number.replace(",", ""))


def supported_values(
    facts: Iterable[Fact],
) -> tuple[frozenset[Decimal], frozenset[str]]:
    numbers: set[Decimal] = set()
    dates: set[str] = set()
    for fact in facts:
        dates.update((utc_iso(fact.event_time), utc_iso(fact.knowledge_time)))
        numbers.update(
            value(found)
            for text in shown_values(fact)
            for found in NUMBER.findall(text)
        )
    return frozenset(numbers), frozenset(dates)


def named_ids(fact: Fact) -> tuple[str, ...]:
    match fact:
        case PaymentFact():
            return (fact.fact_id, fact.record_id, fact.from_account, fact.to_account)
        case SignalFact() | ProfileFact():
            return (fact.fact_id, fact.record_id)
        case _:
            assert_never(fact)


def shown_values(fact: Fact) -> tuple[str, ...]:
    match fact:
        case SignalFact():
            return tuple(text for m in fact.measures for text in (m.value, m.threshold))
        case PaymentFact():
            return (dollars(fact.amount_cents),)
        case ProfileFact():
            return ()
        case _:
            assert_never(fact)
