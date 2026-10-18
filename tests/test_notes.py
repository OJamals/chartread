"""Addenda 2 and 3: scorecards, detection and the pre-registered tests, on hand-built items."""

from __future__ import annotations

from chartread import notes
from chartread.core import ChartItem, Status


def _unit(pair: str, construction: str, status: Status) -> list[ChartItem]:
    return [
        ChartItem(
            item_id=f"chartread:{pair}:{presentation}",
            stratum=notes.STRATUM,
            text=f"FAMILY HISTORY: Positive for diabetes. ({presentation})",
            concept="diabetes",
            status=status,
            construction=construction,
            presentation=presentation,
            pair=pair,
            span=(29, 37),
        )
        for presentation in notes.PRESENTATIONS
    ]


ITEMS = [
    *_unit("mts:1:1:0", notes.FH_BARE, Status.FAMILY),
    *_unit("mts:2:1:0", notes.FH_BARE, Status.FAMILY),
    *_unit("mts:3:5:0", notes.CONTROL, Status.PRESENT),
]


def _header_only() -> dict[str, str | None]:
    """A model that says A unless a family header is in view."""

    return {
        i.item_id: ("B" if i.status is Status.FAMILY and i.presentation != "cut" else "A")
        for i in ITEMS
    }


def test_detection_and_rh1_rh2_on_a_model_that_reads_only_the_header() -> None:
    letters = _header_only()
    card = notes.scorecard(ITEMS, letters)
    assert card["cut"]["far_family"]["hits"] == 2
    assert card["header"]["far_family"]["hits"] == 0
    assert card["cut"]["detection"]["d_prime"] < card["header"]["detection"]["d_prime"]
    result = notes.tests(ITEMS, {"m": letters})
    assert result["pairs"] == 2
    assert result["pooled"]["RH1"]["for"] == 2 and result["pooled"]["RH1"]["against"] == 0
    assert result["pooled"]["RH2"]["for"] == 2
    assert result["pooled"]["RH1"]["holm"] >= result["pooled"]["RH1"]["p"]


def test_addendum_3_keys_cut_windows_c_and_tests_abstention() -> None:
    chosen = notes.abstain_items(ITEMS)
    keys = {i.item_id: notes.abstain_key(i) for i in chosen}
    assert all(keys[i.item_id] == "C" for i in chosen if i.presentation == "cut")
    assert all(keys[i.item_id] == i.answer for i in chosen if i.presentation != "cut")
    requests, key_rows = notes.abstain_requests(chosen)
    assert len(requests) == len(key_rows) == len(chosen)
    assert "C. Cannot tell" in requests[0]["messages"][1]["content"]
    letters = {i.item_id: keys[i.item_id] for i in chosen}
    binary: dict[str, str | None] = {i.item_id: "A" for i in chosen}
    result = notes.abstain_tests(chosen, {"m": letters}, {"m": binary})
    assert result["pooled"]["RAH1"]["for"] == result["pairs"] == 3
    assert result["pooled"]["RAH1"]["against"] == 0
    assert result["pooled"]["RAH2"]["for"] == result["family_cut"] == 2
    card = notes.abstain_scorecard(chosen, letters)
    assert card["cut"]["abstained"]["hits"] == card["cut"]["abstained"]["n"]
    assert card["header"]["abstained"]["hits"] == 0


def test_detection_stays_finite_at_the_extremes() -> None:
    signal = [i for i in ITEMS if i.status is Status.PRESENT]
    noise = [i for i in ITEMS if i.status is Status.FAMILY]
    perfect = {i.item_id: ("A" if i.status is Status.PRESENT else "B") for i in ITEMS}
    result = notes.detection(signal, noise, perfect)
    assert result["hits"] == 3 and result["false_alarms"] == 0
    assert result["d_prime"] > 0
