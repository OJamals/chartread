"""The contamination probe's arithmetic: splitting, ROUGE-L, the echo rule and the sign test."""

from __future__ import annotations

import pytest

from chartread import contamination as ct


def test_rouge_l_is_f1_over_the_longest_common_subsequence() -> None:
    assert ct.rouge_l(["a", "b", "c"], ["a", "b", "c"]) == 1.0
    assert ct.rouge_l([], ["a"]) == 0.0
    assert ct.rouge_l(["x"], ["a"]) == 0.0
    # LCS "a c" of 2: precision 2/3, recall 2/4.
    assert ct.rouge_l(["a", "x", "c"], ["a", "b", "c", "d"]) == pytest.approx(4 / 7)


def test_a_reply_that_repeats_the_first_piece_is_scored_on_the_rest() -> None:
    assert ct.completion("The patient denies chest pain.", "The patient") == [
        "denies",
        "chest",
        "pain",
    ]
    assert ct.completion(None, "x") == []
    assert ct.completion("Denies chest pain", "The patient") == ["denies", "chest", "pain"]


def test_an_instance_shows_its_first_half() -> None:
    passage = " ".join(f"w{i}" for i in range(15))
    instance = ct.split_instance("i", "s", passage)
    assert instance.prefix.split() == [f"w{i}" for i in range(7)]
    assert instance.reference.split() == [f"w{i}" for i in range(7, 15)]


def test_sampling_is_seeded_distinct_and_bounded_by_length() -> None:
    long = " ".join(["word"] * 20)
    pool = [(f"p{i}", f"{long} {i}") for i in range(10)] + [("dup", f"{long} 0"), ("short", "a b")]
    first = ct.sample(pool, 5, seed=1)
    assert first == ct.sample(pool, 5, seed=1)
    assert len({passage for _id, passage in first}) == 5
    assert all(passage_id not in {"dup", "short"} for passage_id, _p in first)
    with pytest.raises(ValueError, match="eligible"):
        ct.sample(pool, 11, seed=1)


def test_the_sign_test_is_one_sided() -> None:
    assert ct.sign_test_greater(0, 0) == 1.0
    assert ct.sign_test_greater(5, 0) == pytest.approx(1 / 32)
    assert ct.sign_test_greater(0, 5) == 1.0


def test_guided_instruction_names_the_source_and_general_does_not() -> None:
    instance = ct.split_instance("i", "negex", " ".join(["w"] * 14))
    guided = ct.render(instance, guided=True, source_name="the corpus")
    general = ct.render(instance, guided=False, source_name="the corpus")
    assert "the corpus" in guided[0]["content"] and "the corpus" not in general[0]["content"]
    assert guided[1] == general[1]


def test_score_compares_the_two_instructions_on_the_same_instance() -> None:
    instances = [
        ct.split_instance(f"i{n}", "negex", " ".join(f"t{n}x{k}" for k in range(14)))
        for n in range(3)
    ]
    replies = {}
    for instance in instances:
        replies[(instance.instance_id, "guided")] = instance.reference
        replies[(instance.instance_id, "general")] = "something else entirely"
    report = ct.score(instances, replies)["negex"]
    assert report["guided"]["near_verbatim"] == 3 and report["general"]["near_verbatim"] == 0
    assert (report["guided_better"], report["general_better"]) == (3, 0)
    assert report["p"] == pytest.approx(1 / 8)
