"""ChartRead: the generator's labels, the real stratum's mapping, the prompt, and the scorer."""

from __future__ import annotations

import csv
import json
from collections import Counter
from pathlib import Path

import pytest

from chartread import core as cr
from chartread.core import ChartItem, Status
from chartread.negex import CorpusCase
from chartread.stats import parse_choice


@pytest.fixture(scope="module")
def items() -> list[ChartItem]:
    return cr.constructed()


def test_generator_is_deterministic_and_ids_unique(items: list[ChartItem]) -> None:
    assert cr.constructed() == items
    assert len({item.item_id for item in items}) == len(items)
    assert cr.constructed(seed=1) != items


def test_every_span_holds_its_finding(items: list[ChartItem]) -> None:
    for item in items:
        start, end = item.span
        assert item.text[start:end].lower() == item.concept.lower(), item.item_id


def test_key_is_a_only_for_present(items: list[ChartItem]) -> None:
    for item in items:
        assert item.answer == ("A" if item.status is Status.PRESENT else "B")


def test_statuses_only_where_they_read_naturally(items: list[ChartItem]) -> None:
    kind_of = {c: kind for kind, concepts in cr.CONCEPTS.items() for c in concepts}
    for item in items:
        if item.construction == "simple":
            assert item.status in cr.COMPATIBLE[kind_of[item.concept]], item.item_id
    symptoms = set(cr.CONCEPTS[cr.SYMPTOM])
    assert not any(i.status is Status.FAMILY and i.concept in symptoms for i in items)
    chronic = set(cr.CONCEPTS[cr.CHRONIC])
    assert not any(i.status is Status.HISTORICAL and i.concept in chronic for i in items)


def test_section_twins_differ_only_in_the_header(items: list[ChartItem]) -> None:
    full = [i for i in items if i.construction == "section" and i.presentation == "full"]
    by_pair: dict[str, list[ChartItem]] = {}
    for item in full:
        by_pair.setdefault(item.pair, []).append(item)
    assert len(by_pair) == sum(len(c) for c in cr.CONCEPTS.values())
    for twins in by_pair.values():
        assert {t.answer for t in twins} == {"A", "B"}
        differing = [
            (x, y)
            for x, y in zip(twins[0].text.splitlines(), twins[1].text.splitlines(), strict=True)
            if x != y
        ]
        assert len(differing) == 1 and differing[0][0].endswith(":")


def test_chunk_cut_loses_the_header_and_chunk_header_keeps_it(items: list[ChartItem]) -> None:
    headers = {h for pair in cr.SECTION_HEADERS.values() for h, _ in pair}
    for item in items:
        if item.presentation == "chunk_cut":
            assert not any(h in item.text for h in headers)
        if item.presentation == "chunk_header":
            assert item.text.splitlines()[0] in headers


def test_list_break_twin_ends_the_cue_before_the_finding(items: list[ChartItem]) -> None:
    breaks = [i for i in items if i.construction == "list_break"]
    negated = {i.pair: i for i in items if i.construction == "list"}
    assert len(breaks) == len(negated) == 2 * len(cr.CONCEPTS[cr.SYMPTOM])
    for item in breaks:
        twin = negated[item.pair]
        assert twin.status is Status.NEGATED and item.status is Status.PRESENT
        assert twin.detail == item.detail


def test_counts_match_the_preregistration(items: list[ChartItem]) -> None:
    counts = Counter((i.construction, i.presentation) for i in items)
    assert counts[("simple", "full")] == 230
    assert counts[("list", "full")] == counts[("list_break", "full")] == 40
    assert counts[("section", "full")] == counts[("section", "chunk_cut")] == 100
    assert counts[("pseudo", "full")] == 70
    assert counts[("two_person", "full")] == 30
    assert len(items) == 710


def _case(n: int, *, negated: bool, temporality: str = "Recent", who: str = "Patient",
          sentence: str = "Patient has PNEUMONIA.") -> CorpusCase:  # fmt: skip
    start = sentence.lower().index("pneumonia")
    return CorpusCase(
        identifier=f"{n}:{n}",
        sentence=sentence,
        concept="pneumonia",
        start=start,
        end=start + len("pneumonia"),
        gold_negated=negated,
        temporality=temporality,
        experiencer=who,
    )


def test_negex_mapping_drops_what_the_key_would_argue_about() -> None:
    assert cr.negex_status(_case(1, negated=False)) is Status.PRESENT
    assert cr.negex_status(_case(2, negated=True)) is Status.NEGATED
    assert cr.negex_status(_case(3, negated=True, temporality="Historical")) is Status.NEGATED
    assert cr.negex_status(_case(4, negated=False, who="Family member")) is Status.FAMILY
    assert cr.negex_status(_case(5, negated=False, temporality="Historical")) is None
    assert cr.negex_status(_case(6, negated=False, temporality="Not particular")) is None
    hedged = _case(7, negated=False, sentence="Findings may represent PNEUMONIA.")
    assert cr.negex_status(hedged) is None
    assert cr.negex_status(_case(8, negated=False, sentence="Possible PNEUMONIA.")) is None


def test_negex_items_balance_affirmed_against_negated() -> None:
    cases = [_case(i, negated=i % 4 == 0) for i in range(40)]
    items, dropped = cr.negex_items(cases, seed=3)
    statuses = Counter(item.status for item in items)
    assert statuses[Status.PRESENT] == statuses[Status.NEGATED] == 10
    assert dropped["affirmed_not_sampled"] == 20
    assert all(item.pair == "" and item.construction == "real" for item in items)


def test_prompt_offers_two_options_and_names_the_finding(items: list[ChartItem]) -> None:
    messages = cr.render(items[0])
    assert messages[0]["role"] == "system" and "Answer: X" in messages[0]["content"]
    user = messages[1]["content"]
    assert f'"{items[0].concept}"' in user and items[0].text in user
    assert "\nA. " in user and "\nB. " in user and "\nC. " not in user
    assert parse_choice("The finding is denied.\nAnswer: B", cr.LETTERS) == "B"


def test_prepare_writes_harness_files(tmp_path: Path, items: list[ChartItem]) -> None:
    counts = cr.prepare(items[:5], tmp_path)
    assert counts == {"requests": 5, "keys": 5, "items": 5}
    key = json.loads((tmp_path / "keys.jsonl").read_text().splitlines()[0])
    assert key["letters"] == ["A", "B"] and key["answer"] == items[0].answer
    assert cr.load_items(tmp_path) == items[:5]


def _responses(letters: dict[str, str | None]) -> list[dict[str, object]]:
    return [
        {"request_id": rid, "text": "" if letter is None else f"Answer: {letter}", "error": None}
        for rid, letter in letters.items()
    ]


def test_far_counts_affirmations_and_unanswered_stay_in_the_denominator(
    items: list[ChartItem],
) -> None:
    sample = [i for i in items if i.construction == "simple"][:20]
    negatives = [i for i in sample if i.status is not Status.PRESENT]
    given: dict[str, str | None] = {i.item_id: i.answer for i in sample}
    given[negatives[0].item_id] = "A"
    given[negatives[1].item_id] = None
    letters = cr.answers(sample, _responses(given))
    rate = cr.far(sample, letters)
    assert (rate.hits, rate.n, rate.unanswered) == (1, len(negatives), 1)
    assert cr.fdr(sample, letters).hits == 0


def test_pair_consistency_needs_every_twin_right(items: list[ChartItem]) -> None:
    pseudo = [i for i in items if i.construction == "pseudo"][:4]
    given: dict[str, str | None] = {i.item_id: i.answer for i in pseudo}
    given[pseudo[0].item_id] = "B" if pseudo[0].answer == "A" else "A"
    rate = cr.pair_consistency(pseudo, cr.answers(pseudo, _responses(given)))
    assert (rate.hits, rate.n) == (1, 2)


def test_hypotheses_run_on_the_full_set(items: list[ChartItem]) -> None:
    always_b: dict[str, str | None] = {i.item_id: "B" for i in items}
    affirm_cut: dict[str, str | None] = {
        i.item_id: "A" if i.presentation == "chunk_cut" else "B" for i in items
    }
    results = cr.hypothesis_tests(items, {"x": always_b, "y": affirm_cut})
    assert set(results["holm"]) == {"H1", "H2", "H3_chunk_cut"}
    assert results["H3_chunk_cut"]["pairs"] == 50
    assert results["H3_chunk_cut"]["pooled"]["only_chunk_cut"] == 50
    assert results["H3_chunk_cut"]["pooled"]["p"] < 1e-10


def test_holm_and_cmh() -> None:
    adjusted = cr.holm({"a": 0.01, "b": 0.04, "c": 0.03})
    assert adjusted == {"a": 0.03, "c": 0.06, "b": 0.06}
    stronger = cr.cmh([(20, 80, 5, 95), (18, 82, 4, 96)])
    assert stronger["odds_ratio"] > 3 and stronger["p"] < 0.001
    assert cr.cmh([(10, 90, 10, 90)])["p"] > 0.5


def test_packet_is_blind_and_agreement_reads_it_back(
    tmp_path: Path, items: list[ChartItem]
) -> None:
    chosen = cr.stratified_sample([i for i in items if i.presentation == "full"], 30)
    assert len({cr.cell(i) for i in chosen}) >= 12
    sheet = tmp_path / "rate.csv"
    mapping = cr.write_packet(chosen, sheet, "C")
    header = sheet.read_text().splitlines()[0]
    assert header == ",".join(cr.PACKET_FIELDS)
    for forbidden in ("PRESENT", "NEGATED", "section", "pseudo"):
        assert forbidden not in header
    rows = list(csv.DictReader(sheet.open()))
    by_id = {i.item_id: i for i in chosen}
    for name, flip in (("a", 0), ("b", 2)):
        filled = tmp_path / f"{name}.csv"
        with filled.open("w", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=cr.PACKET_FIELDS)
            writer.writeheader()
            for index, row in enumerate(rows):
                answer = by_id[mapping[row["ref"]]].answer
                if index < flip:
                    answer = "A" if answer == "B" else "B"
                writer.writerow({**row, "answer": answer})
    report = cr.agreement(
        mapping, by_id, {n: cr.read_ratings(tmp_path / f"{n}.csv") for n in ("a", "b")}
    )
    assert report["raters"]["a"]["agreement"] == 1.0
    assert report["raters"]["b"]["agree_with_key"] == len(chosen) - 2
    assert report["disputed_by_both"] == []
    assert report["between_raters"]["shared"] == len(chosen)


def test_cohen_kappa() -> None:
    assert cr.cohen_kappa(["A", "B", "A", "B"], ["A", "B", "A", "B"]) == 1.0
    assert cr.cohen_kappa(["A", "A", "B", "B"], ["A", "B", "A", "B"]) == 0.0
    assert cr.cohen_kappa([], []) is None


def test_abstain_set_keys_cut_windows_as_cannot_tell(items: list[ChartItem]) -> None:
    section = [i for i in items if i.construction == "section"]
    keys = Counter(cr.abstain_key(i) for i in section)
    assert keys == {"A": 100, "B": 100, "C": 100}
    assert all(cr.abstain_key(i) == "C" for i in section if i.presentation == "chunk_cut")
    user = cr.render_abstain(section[0])[1]["content"]
    assert "\nA. " in user and "\nB. " in user and "\nC. " in user
    assert "states it is not currently present" in user


def test_abstain_prepare_writes_three_letter_keys(tmp_path: Path, items: list[ChartItem]) -> None:
    section = [i for i in items if i.construction == "section"][:6]
    cr.prepare_abstain(section, tmp_path)
    key = json.loads((tmp_path / "keys.jsonl").read_text().splitlines()[0])
    assert key["letters"] == ["A", "B", "C"]
    request = json.loads((tmp_path / "requests.jsonl").read_text().splitlines()[0])
    assert request["prompt_version"] == cr.ABSTAIN_PROMPT_VERSION


def test_abstain_scorecard_and_tests(items: list[ChartItem]) -> None:
    section = [i for i in items if i.construction == "section"]
    ideal: dict[str, str | None] = {i.item_id: cr.abstain_key(i) for i in section}
    letters = cr.abstain_answers(section, _responses(ideal))
    card = cr.abstain_scorecard(section, letters)
    assert card["chunk_cut"]["abstained"]["rate"] == 1.0
    assert card["full"]["abstained"]["rate"] == 0.0
    assert card["full"]["far"]["hits"] == 0 and card["chunk_cut"]["correct"]["rate"] == 1.0
    binary = {i.item_id: "A" for i in section}
    results = cr.abstain_tests(section, {"x": letters}, {"x": binary})
    assert results["pooled"]["AH1"]["for"] == 100 and results["pooled"]["AH1"]["against"] == 0
    assert results["pooled"]["AH2"]["for"] == 50 and results["pooled"]["AH2"]["against"] == 0


def test_audit_flags_name_the_template_defects_and_spare_their_twins(
    items: list[ChartItem],
) -> None:
    census = cr.audit_census(items)
    assert census["constructed"]["by_flag"] == {
        "sex_discordant": 6,
        "acute_active_list": 30,
        "relief_of_event": 1,
        "control_without_target": 4,
        "conditional_cancer": 3,
        "real_unkeyable": 0,
    }
    by_id = {i.item_id: i for i in items}
    assert cr.audit_flags(by_id["chartread:c:two_person:prostate-cancer:present:full"]) == (
        "sex_discordant",
    )
    # A section pair's resolved twin, and its header window with no routine-visit plan, read
    # naturally: only the acute list called active in a three-month follow-up is flagged.
    sepsis = {(i.status, i.presentation): i for i in items if i.pair == "section:sepsis"}
    assert cr.audit_flags(sepsis[(Status.PRESENT, "full")]) == ("acute_active_list",)
    assert cr.audit_flags(sepsis[(Status.PRESENT, "chunk_cut")]) == ("acute_active_list",)
    assert not cr.audit_flags(sepsis[(Status.PRESENT, "chunk_header")])
    assert not cr.audit_flags(sepsis[(Status.HISTORICAL, "full")])


def test_audit_flags_real_items_by_corpus_row_only() -> None:
    def real(item_id: str) -> ChartItem:
        return ChartItem(item_id, "negex", "No masses, SPLENOMEGALY.", "splenomegaly",
                         Status.NEGATED, "real", "full", "", (12, 24))  # fmt: skip

    assert cr.audit_flags(real("chartread:negex:1902:1901")) == ("real_unkeyable",)
    assert cr.audit_flags(real("chartread:negex:1:0")) == ()


def test_audit_scorecard_excludes_flagged_items_from_both_counts(
    items: list[ChartItem],
) -> None:
    always_a: dict[str, str | None] = {i.item_id: "A" for i in items}
    card = cr.audit_scorecard(items, always_a)["constructed"]
    full = [i for i in items if i.presentation == "full"]
    flagged_negatives = sum(bool(cr.audit_flags(i)) for i in full if i.status is not Status.PRESENT)
    assert card["all"]["far"]["hits"] == card["all"]["far"]["n"]
    assert card["all"]["far"]["n"] - card["unflagged"]["far"]["n"] == flagged_negatives
    assert card["unflagged"]["fdr"]["hits"] == 0


def test_v1_is_still_the_released_set(items: list[ChartItem]) -> None:
    released = Path(__file__).resolve().parents[1] / "data/v1/constructed.jsonl"
    rows = [json.loads(line) for line in released.read_text("utf-8").splitlines()]
    assert [item.as_row() for item in items] == rows


def test_v11_changes_only_what_the_audit_found(items: list[ChartItem]) -> None:
    fixed = cr.constructed(version="v1.1")
    assert [i.item_id for i in fixed] == [i.item_id for i in items]
    changed = [(old, new) for old, new in zip(items, fixed, strict=True) if old != new]
    assert len(changed) == 93
    for old, new in changed:
        assert (old.status, old.concept, old.construction, old.presentation, old.pair) == (
            new.status,
            new.concept,
            new.construction,
            new.presentation,
            new.pair,
        )
        start, end = new.span
        assert new.text[start:end].lower() == new.concept.lower()
    flagged = {i.item_id for i in items if cr.audit_flags(i)}
    assert flagged <= {old.item_id for old, _ in changed}
    assert not any(cr.audit_flags(i) for i in fixed)
    # The header window carries no note frame, so the acute fix leaves it as v1 wrote it.
    assert all(old.presentation != "chunk_header" for old, _ in changed)


def test_v11_drops_only_the_unkeyable_real_rows() -> None:
    def case(row: int) -> CorpusCase:
        return CorpusCase(
            identifier=f"{row + 1}:{row}",
            sentence="No masses, splenomegaly.",
            concept="splenomegaly",
            start=11,
            end=23,
            gold_negated=True,
            temporality="Recent",
            experiencer="Patient",
        )

    cases = [case(1901), case(5)]
    v1, _ = cr.negex_items(cases)
    v11, dropped = cr.negex_items(cases, version="v1.1")
    assert {i.item_id for i in v1} - {i.item_id for i in v11} == {"chartread:negex:1902:1901"}
    assert dropped["unkeyable_audit"] == 1


def _completed(sentence: str, concept: str) -> bool:
    start = sentence.index(concept)
    return cr.completed(sentence, (start, start + len(concept)))


@pytest.mark.parametrize(
    ("sentence", "concept"),
    [
        ("No residua of HEALED FRACTURES can be seen.", "HEALED FRACTURES"),
        ("Median sternotomy and resection of a MALIGNANT THYMOMA.", "MALIGNANT THYMOMA"),
        ("A man with a history of THYMOMA.", "THYMOMA"),
        ("LEFT HIP FRACTURE STATUS POST HEMIARTHROPLASTY.", "LEFT HIP FRACTURE"),
        ("A 5mm SESSILE POLYP and  removed with snare polypectomy.", "SESSILE POLYP"),
        ("She had RUPTURED ANEURYSM and underwent a coiling.", "RUPTURED ANEURYSM"),
        ("Pneumonia, resolved.", "Pneumonia"),
    ],
)
def test_a_completed_treatment_cue_on_the_finding_is_found(sentence: str, concept: str) -> None:
    assert _completed(sentence, concept)


@pytest.mark.parametrize(
    ("sentence", "concept"),
    [
        # Scoped to the finding: the sentence-level rule rejected for v1.1 matched these.
        ("HISTORY OF PRESENT ILLNESS: The patient has COUGH.", "COUGH"),
        ("HIP FRACTURE status post fall.", "HIP FRACTURE"),
        # A duration says how long, not that it is over.
        ("She has a two-day history of COUGH.", "COUGH"),
        ("Aspirin was held prior to SURGERY.", "SURGERY"),
        # Read off corpus rows outside the sample, before v1.2 was written.
        ("This is a 70s-year-old DEMENTED woman.", "DEMENTED"),
        (
            "She was found to have LEFT LOWER EXTREMITY DVT, status post IVC placement.",
            "LEFT LOWER EXTREMITY DVT",
        ),
    ],
)
def test_a_cue_that_does_not_end_the_finding_is_not_completed(sentence: str, concept: str) -> None:
    assert not _completed(sentence, concept)


def test_v12_drops_completed_present_rows_and_keeps_v11s_constructed_stratum(
    items: list[ChartItem],
) -> None:
    def case(row: int, sentence: str, concept: str, negated: bool) -> CorpusCase:
        start = sentence.index(concept)
        return CorpusCase(
            identifier=f"{row + 1}:{row}",
            sentence=sentence,
            concept=concept,
            start=start,
            end=start + len(concept),
            gold_negated=negated,
            temporality="Recent",
            experiencer="Patient",
        )

    cases = [
        case(10, "IMPRESSION: HEALED FRACTURES.", "HEALED FRACTURES", False),
        case(11, "IMPRESSION: PNEUMONIA.", "PNEUMONIA", False),
        case(12, "No HEALED FRACTURES.", "HEALED FRACTURES", True),
        case(13, "No EFFUSION.", "EFFUSION", True),
    ]
    v11, _ = cr.negex_items(cases, version="v1.1")
    v12, dropped = cr.negex_items(cases, version="v1.2")
    assert {i.item_id for i in v11} - {i.item_id for i in v12} == {"chartread:negex:11:10"}
    assert dropped["completed_treatment"] == 1
    assert cr.constructed(version="v1.2") == cr.constructed(version="v1.1")


def test_unknown_version_is_refused() -> None:
    with pytest.raises(ValueError, match="unknown ChartRead version"):
        cr.constructed(version="v2")


def test_crosswalk_covers_every_status_and_keys_agree() -> None:
    assert set(cr.CROSSWALK) == set(Status)
    assert cr.CROSSWALK[Status.FAMILY]["i2b2_2010"] == "associated_with_someone_else"
    assert cr.CROSSWALK[Status.HISTORICAL]["fhir_r4"].startswith(
        "Condition.clinicalStatus=resolved"
    )
