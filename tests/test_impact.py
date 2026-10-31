"""Addendum 4: a chunk exposes a unit when it holds the statement and none of its governors."""

from __future__ import annotations

from chartread import impact
from chartread.stats import clustered_paired


def _unit(span: tuple[int, int], governor: tuple[int, int]) -> impact.Unit:
    return impact.Unit(
        unit_id="n:0:0",
        note="corpus/n",
        form="as_written",
        kind=impact.NEG_LIST,
        span=span,
        governors=(governor,),
        findings=(span,),
    )


def test_a_chunk_that_keeps_the_cue_does_not_expose_the_finding() -> None:
    unit = _unit((20, 30), (10, 16))
    assert impact.exposure(unit, [(0, 40)]) == impact.Exposure(containing=1, exposing=0)


def test_a_chunk_that_starts_after_the_cue_exposes_the_finding() -> None:
    unit = _unit((20, 30), (10, 16))
    assert impact.exposure(unit, [(0, 18), (17, 40)]) == impact.Exposure(containing=1, exposing=1)


def test_a_statement_no_chunk_holds_is_split_not_exposed() -> None:
    seen = impact.exposure(_unit((20, 30), (10, 16)), [(0, 25), (25, 40)])
    assert seen.split and seen.exposing == 0


def test_burden_weights_a_finding_by_the_share_of_chunks_that_expose_it() -> None:
    unit = _unit((20, 30), (10, 16))
    seen = impact.exposure(unit, [(0, 40), (17, 40)])
    owned = impact.burden([(unit, seen)], notes=2)
    assert owned["exposed"] == {"low": 0, "mid": 0.5, "high": 1}
    estimate = impact.per_hundred(owned, {"rate": 0.5, "ci95": [0.25, 0.75]})
    assert estimate == {"point": 12.5, "range": [0.0, 37.5], "far": 0.5}


def test_clustered_counts_an_item_once_however_many_arms_read_it() -> None:
    clusters = {"a": [(True, False)] * 5, "b": [(True, False), (False, True)], "c": [(False, True)]}
    report = clustered_paired(clusters, resamples=200)
    assert (report["items"], report["positive"], report["negative"], report["ties"]) == (3, 1, 1, 1)
    assert report["trials"] == 8
    assert report["difference"] == round((5 + 0 - 1) / 8, 4)
