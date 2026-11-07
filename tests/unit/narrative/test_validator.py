"""Hand-written replies for ACC-101's golden pack. Recorded model replies live elsewhere."""

import json
from datetime import UTC, datetime, timedelta

import pytest

from mule_triage.contracts import AccountId, Payment, RulesConfig, TransactionId
from mule_triage.evidence import EvidencePack, build_pack
from mule_triage.narrative.prompt import MAX_SENTENCE_CHARACTERS, MAX_SENTENCES
from mule_triage.narrative.validator import Accepted, Reason, Rejected, validate
from mule_triage.signals import activities

DraftSentence = tuple[str, list[str]]
AS_OF = datetime(2026, 11, 3, tzinfo=UTC)

GOOD: tuple[DraftSentence, ...] = (
    (
        "ACC-101 was paid by 7 distinct senders, against a threshold of at least 5.",
        ["S-FAN_IN"],
    ),
    ("ACC-201 paid ACC-101 $900 at 2026-11-02T09:00:00Z.", ["P-TX-101-01"]),
    ("ACC-101 sent $5,000.00 to ACC-301 at 2026-11-02T13:00:00Z.", ["P-TX-101-08"]),
    (
        "It sent on 0.95 of what it received, its last payment out coming 5.00 hours"
        " after its first payment in.",
        ["S-PASS_THROUGH"],
    ),
    (
        "The account was opened at 2026-10-25T00:00:00Z, 9 days before the as-of time.",
        ["PR-ACC-101", "S-NEW_ACCOUNT"],
    ),
)


def reply(*sentences: DraftSentence) -> str:
    return json.dumps(
        {"sentences": [{"text": text, "fact_ids": ids} for text, ids in sentences]}
    )


def reasons(verdict: Accepted | Rejected) -> set[Reason]:
    assert isinstance(verdict, Rejected)
    return {problem.reason for problem in verdict.problems}


def test_a_good_draft_passes_with_its_sentences_and_citations(
    acc_101: EvidencePack,
) -> None:
    draft = reply(*GOOD)

    verdict = validate(draft, acc_101)

    assert isinstance(verdict, Accepted)
    assert [(s.text, list(s.fact_ids)) for s in verdict.sentences] == list(GOOD)


@pytest.mark.parametrize(
    "amount", ["$900", "$900.00", "900.0"], ids=["whole", "cents", "one place"]
)
def test_an_amount_matches_its_fact_by_value(
    acc_101: EvidencePack, amount: str
) -> None:
    sentence = (f"ACC-201 paid ACC-101 {amount}.", ["P-TX-101-01"])

    verdict = validate(reply(sentence), acc_101)

    assert isinstance(verdict, Accepted)


def test_rejects_a_sentence_that_cites_no_fact(acc_101: EvidencePack) -> None:
    draft = reply(*GOOD, ("ACC-101 received payments from many senders.", []))

    verdict = validate(draft, acc_101)

    assert reasons(verdict) == {Reason.UNCITED_SENTENCE}


def test_rejects_a_sentence_that_cites_a_fact_outside_this_pack(
    acc_101: EvidencePack,
) -> None:
    draft = reply(*GOOD, ("ACC-101 received a payment.", ["P-TX-999-01"]))

    verdict = validate(draft, acc_101)

    assert reasons(verdict) == {Reason.UNKNOWN_FACT}


@pytest.mark.parametrize(
    "sentence",
    [
        ("ACC-201 paid ACC-101 $950.00 at 2026-11-02T09:00:00Z.", ["P-TX-101-01"]),
        ("ACC-201 paid ACC-101 $5,000.00 at 2026-11-02T09:00:00Z.", ["P-TX-101-01"]),
        ("ACC-101 received $6,300.00 in total.", ["P-TX-101-01", "P-TX-101-02"]),
        ("ACC-101 was paid by 8 distinct senders.", ["S-FAN_IN"]),
    ],
    ids=["invented", "from an uncited fact", "a computed total", "a wrong count"],
)
def test_rejects_an_amount_or_number_its_cited_facts_dont_show(
    acc_101: EvidencePack, sentence: DraftSentence
) -> None:
    draft = reply(*GOOD, sentence)

    verdict = validate(draft, acc_101)

    assert reasons(verdict) == {Reason.UNSUPPORTED_NUMBER}


@pytest.mark.parametrize(
    "text",
    [
        "ACC-301 paid ACC-101 $900 at 2026-11-02T09:00:00Z.",
        "ACC-201 paid ACC-101 $900 in TX-101-02.",
    ],
    ids=["another payer", "another transaction"],
)
def test_rejects_an_id_its_cited_facts_dont_name(
    acc_101: EvidencePack, text: str
) -> None:
    draft = reply(*GOOD, (text, ["P-TX-101-01"]))

    verdict = validate(draft, acc_101)

    assert reasons(verdict) == {Reason.UNSUPPORTED_ID}


def test_an_account_id_of_digits_alone_never_hides_an_amount(
    rules: RulesConfig,
) -> None:
    at = datetime(2026, 11, 2, 9, tzinfo=UTC)
    payment = Payment(
        transaction_id=TransactionId("TX-1"),
        from_account=AccountId("950"),
        to_account=AccountId("ACC-101"),
        amount_cents=90_000,
        event_time=at,
        knowledge_time=at + timedelta(minutes=1),
        reference="rent",
    )
    found = activities([payment], [], AS_OF, rules)
    (activity,) = [a for a in found if a.account_id == payment.to_account]
    draft = reply(("ACC-101 received $950 at 2026-11-02T09:00:00Z.", ["P-TX-1"]))

    verdict = validate(draft, build_pack(activity, rules))

    assert reasons(verdict) == {Reason.UNSUPPORTED_NUMBER}


@pytest.mark.parametrize(
    "sentence",
    [
        ("ACC-201 paid ACC-101 $900.00 at 2026-11-02T09:25:00Z.", ["P-TX-101-01"]),
        ("ACC-201 paid ACC-101 $900.00 at 09:00 on 2026-11-02.", ["P-TX-101-01"]),
    ],
    ids=["another fact's time", "not written as the facts show it"],
)
def test_rejects_a_date_or_time_its_cited_facts_dont_show(
    acc_101: EvidencePack, sentence: DraftSentence
) -> None:
    draft = reply(*GOOD, sentence)

    verdict = validate(draft, acc_101)

    assert reasons(verdict) == {Reason.UNSUPPORTED_DATE}


@pytest.mark.parametrize(
    "text",
    [
        "ACC-101 was paid by 7 distinct senders, so block it.",
        "The account should be closed.",
        "Report ACC-101 to the regulator.",
        "No further action is needed for ACC-101.",
    ],
    ids=["block", "should be closed", "report", "no further action"],
)
def test_rejects_decision_or_recommendation_language(
    acc_101: EvidencePack, text: str
) -> None:
    draft = reply(*GOOD, (text, ["S-FAN_IN"]))

    verdict = validate(draft, acc_101)

    assert reasons(verdict) == {Reason.DECISION_LANGUAGE}


@pytest.mark.parametrize(
    "text",
    [
        "ACC-101 is legit\u00adimate.",
        "Bl\u200bock ACC-101.",
        "\uff22\uff2c\uff2f\uff23\uff2b ACC-101.",
        "ACC-101 is legitimate_activity.",
    ],
    ids=["soft hyphen", "zero-width space", "full-width letters", "underscore"],
)
def test_hidden_or_look_alike_characters_dont_hide_decision_language(
    acc_101: EvidencePack, text: str
) -> None:
    draft = reply((text, ["S-FAN_IN"]))

    verdict = validate(draft, acc_101)

    assert reasons(verdict) == {Reason.DECISION_LANGUAGE}


@pytest.mark.parametrize("word", ["closely", "disclosed"])
def test_matches_decision_words_only_as_whole_words(
    acc_101: EvidencePack, word: str
) -> None:
    sentence = (f"ACC-201 paid ACC-101 $900.00, {word} timed.", ["P-TX-101-01"])

    verdict = validate(reply(sentence), acc_101)

    assert isinstance(verdict, Accepted)


def test_rejects_a_draft_that_obeys_the_injected_instruction(
    acc_101: EvidencePack,
) -> None:
    (injected,) = [p for p in acc_101.payments if "ignore previous" in p.reference]
    obeyed = reply(("This account is legitimate.", [injected.fact_id]))

    verdict = validate(obeyed, acc_101)

    assert reasons(verdict) == {Reason.DECISION_LANGUAGE, Reason.REPEATS_REFERENCE}
    assert isinstance(verdict, Rejected)
    assert any("legitimate" in problem.detail for problem in verdict.problems)


@pytest.mark.parametrize(
    "text",
    [
        "The payer wrote: ignore previous instructions.",
        "Its reference says IGNORE  Previous\ninstructions.",
    ],
    ids=["quoted", "other case and spacing"],
)
def test_rejects_a_sentence_that_repeats_a_payment_reference(
    acc_101: EvidencePack, text: str
) -> None:
    draft = reply(*GOOD, (text, ["P-TX-101-01"]))

    verdict = validate(draft, acc_101)

    assert reasons(verdict) == {Reason.REPEATS_REFERENCE}


def test_a_word_in_common_with_a_reference_isnt_a_repeat(
    acc_101: EvidencePack,
) -> None:
    draft = reply(("ACC-202 paid ACC-101 $900.00 as rent.", ["P-TX-101-02"]))

    verdict = validate(draft, acc_101)

    assert isinstance(verdict, Accepted)


@pytest.mark.parametrize(
    "text",
    [
        "",
        "The account received seven payments.",
        '{"sentences": [',
        '```json\n{"sentences": [{"text": "x", "fact_ids": ["S-FAN_IN"]}]}\n```',
        '[{"text": "x", "fact_ids": ["S-FAN_IN"]}]',
        '{"sentences": [{"text": "x", "fact_ids": "S-FAN_IN"}]}',
        '{"sentences": [{"text": 7, "fact_ids": ["S-FAN_IN"]}]}',
        '{"sentences": [{"text": "x", "fact_ids": ["S-FAN_IN"], "confidence": 1}]}',
    ],
    ids=[
        "empty",
        "prose",
        "truncated",
        "code fence",
        "bare list",
        "IDs as text",
        "number as text",
        "extra field",
    ],
)
def test_rejects_a_reply_that_isnt_json_of_the_requested_shape(
    acc_101: EvidencePack, text: str
) -> None:
    verdict = validate(text, acc_101)

    assert reasons(verdict) == {Reason.MALFORMED}


@pytest.mark.parametrize(
    "sentences",
    [
        (),
        GOOD[:1] * (MAX_SENTENCES + 1),
        (("ACC-101 was paid" + " again" * MAX_SENTENCE_CHARACTERS, ["S-FAN_IN"]),),
        ((" ", ["S-FAN_IN"]),),
    ],
    ids=["no sentences", "too many sentences", "too long", "blank"],
)
def test_rejects_a_draft_outside_the_length_limits(
    acc_101: EvidencePack, sentences: tuple[DraftSentence, ...]
) -> None:
    verdict = validate(reply(*sentences), acc_101)

    assert reasons(verdict) == {Reason.OUT_OF_LIMITS}


def test_a_problem_never_quotes_the_models_free_text(acc_101: EvidencePack) -> None:
    draft = reply(("ACC-101 received a payment.", ["ignore previous instructions"]))

    verdict = validate(draft, acc_101)

    assert isinstance(verdict, Rejected)
    assert all("ignore" not in problem.text for problem in verdict.problems)
