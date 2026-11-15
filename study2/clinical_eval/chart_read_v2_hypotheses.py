"""ChartRead v2's seven registered tests (``docs/reference/chartread-v2-preregistration.md``).

Written after registration and before any response is scored, as the registration requires. The
registration froze ``chart_read_v2_run.py`` by digest, so the tests live here and read answers
only through that module's reader — a second reader could disagree with the descriptive report
about what a reply said.

Each test is an exact McNemar test over paired units: per arm, then pooled over the model arms by
summed discordant pairs (addendum 1's method). A reference arm (Layer 2) is reported beside them
and never pooled. Pass 1 is the registered draw; where a prompt has later passes, the test is
repeated on each as a sensitivity analysis. Holm's correction runs across the seven pooled
pass-1 p-values. The item-clustered comparison of v1's deviation 24 is reported beside each
pooled test as a sensitivity analysis, never in place of it.

Where a test compares errors, an unparsed reply is an error. Where it compares what was said on a
cut window, which has no binary key, an unparsed reply did not say "present"; such replies are
counted beside the test.
"""

from __future__ import annotations

import hashlib
import math
import random
import subprocess
from collections import defaultdict
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

from clinical_eval import chart_read as cr
from clinical_eval import chart_read_v2_run as run
from clinical_eval.chart_read import Status
from clinical_eval.chart_read_repeat import message_groups
from clinical_eval.chart_read_v2 import DraftItem
from clinical_eval.scoring import (
    BOOTSTRAP_RESAMPLES,
    BOOTSTRAP_SEED,
    clustered_paired,
    mcnemar_exact,
)

PREREGISTRATION: Final = (
    Path(__file__).resolve().parent.parent / "docs/reference/chartread-v2-preregistration.md"
)
#: Recorded in bd (``chartread-v2-preregistration``) on 2026-11-02.
PREREGISTRATION_SHA256: Final = "c2d95c0cf3ab56486f25de74581a629ee04cfb5fb2373d0955c47c9bfa10d058"

#: Layer 2's registered baseline: ``git rev-parse <commit>:clinical_preprocessor``, a tree id, so
#: any commit carrying the same code qualifies.
LAYER2_BASELINE_TREE: Final = "38438b90f706e36a9523e9842efbb8cd821b3fa5"

GREATER: Final = "greater"
TWO_SIDED: Final = "two-sided"

#: One paired unit: (event in the tested condition, event in the reference condition).
Unit = tuple[bool, bool]
#: One pass's letters under each prompt it reads, keyed by prompt name.
Letters = Mapping[str, Mapping[str, str | None]]


@dataclass(frozen=True, slots=True)
class Units:
    pairs: dict[str, Unit]
    #: Units left out because a reply did not land.
    missing: int = 0
    #: Replies that landed and committed to no letter.
    unparsed: int = 0


@dataclass(frozen=True, slots=True)
class Test:
    name: str
    claim: str
    direction: str
    #: The prompts whose answers the test reads.
    prompts: tuple[str, ...]
    units: Callable[[Mapping[str, Sequence[DraftItem]], Letters], Units]


def one_sided(tested: int, reference: int) -> float:
    """Exact one-sided McNemar p-value that the tested condition carries the event more often."""

    n = tested + reference
    if n == 0:
        return 1.0
    return min(1.0, float(sum(math.comb(n, i) for i in range(tested, n + 1))) * 0.5**n)


def p_value(direction: str, tested: int, reference: int) -> float:
    if direction == GREATER:
        return one_sided(tested, reference)
    return mcnemar_exact(tested, reference)


# --- what one pass says about an item ---------------------------------------------------------


def _wrong(d: DraftItem, prompt: str, letters: Letters) -> bool | None:
    """Whether ``d`` was answered wrongly under ``prompt``: ``None`` where no reply landed. An
    unparsed reply is an error."""

    read = letters[prompt]
    if d.item.item_id not in read:
        return None
    return read[d.item.item_id] != run.answer(d, run.PROMPTS[prompt])


def _paired_errors(
    tested: Mapping[str, DraftItem],
    reference: Mapping[str, DraftItem],
    pairing: Mapping[str, str],
    prompt: str,
    letters: Letters,
) -> Units:
    """Error against error, for every tested unit and the reference item it is paired with."""

    unkeyed = sorted(
        d.item.item_id
        for d in [*tested.values(), *reference.values()]
        if not run.keyed(d, run.PROMPTS[prompt])
    )
    if unkeyed:
        raise ValueError(f"a registered test would score unkeyed items: {unkeyed[:5]}")
    pairs: dict[str, Unit] = {}
    missing = unparsed = 0
    read = letters[prompt]
    for unit, item_id in sorted(pairing.items()):
        a = _wrong(tested[unit], prompt, letters)
        b = _wrong(reference[item_id], prompt, letters)
        if a is None or b is None:
            missing += 1
            continue
        unparsed += sum(read[i] is None for i in (tested[unit].item.item_id, item_id))
        pairs[unit] = (a, b)
    return Units(pairs, missing, unparsed)


def _by_template(items: Sequence[DraftItem]) -> dict[tuple[str, str], DraftItem]:
    return {(d.template_id, d.item.concept): d for d in items}


def _template_pairs(
    items: Sequence[DraftItem], tested: Sequence[str], reference: str
) -> tuple[dict[str, DraftItem], dict[str, DraftItem], dict[str, str]]:
    """Each tested template's item paired with the same concept's reference-template item."""

    index = _by_template(items)
    concepts = sorted({c for t, c in index if t == reference})
    tested_items: dict[str, DraftItem] = {}
    reference_items: dict[str, DraftItem] = {}
    pairing: dict[str, str] = {}
    for concept in concepts:
        ref = index[(reference, concept)]
        reference_items[ref.item.item_id] = ref
        for template in tested:
            d = index.get((template, concept))
            if d is None:
                continue
            unit = f"{template}:{concept}"
            tested_items[unit] = d
            pairing[unit] = ref.item.item_id
    return tested_items, reference_items, pairing


# --- the seven units --------------------------------------------------------------------------


def depth_units(items: Mapping[str, Sequence[DraftItem]], letters: Letters) -> Units:
    """H1: the same concept, status and position at 6,000 words of filler against 750."""

    cells: dict[tuple[str, str, str], dict[int, DraftItem]] = defaultdict(dict)
    for d in items["binary"]:
        if d.item.construction != "depth":
            continue
        concept, words, position = d.item.pair.rsplit(":", 3)[1:]
        if int(words) in (750, 6000):
            cells[(concept, d.item.status.value, position)][int(words)] = d
    tested = {":".join(k): cell[6000] for k, cell in cells.items()}
    reference = {cell[750].item.item_id: cell[750] for cell in cells.values()}
    pairing = {":".join(k): cell[750].item.item_id for k, cell in cells.items()}
    return _paired_errors(tested, reference, pairing, "binary", letters)


def _template_units(
    tested: Sequence[str], reference: str
) -> Callable[[Mapping[str, Sequence[DraftItem]], Letters], Units]:
    def units(items: Mapping[str, Sequence[DraftItem]], letters: Letters) -> Units:
        return _paired_errors(
            *_template_pairs(items["binary"], tested, reference), "binary", letters
        )

    return units


def _said_present(prompt: str, letter: str | None) -> bool:
    return letter == run.PROMPTS[prompt].yes


def _cut_messages(items: Sequence[DraftItem], parts: frozenset[str]) -> list[str]:
    """Each distinct cut-window message once, by its lowest request id (as addendum 6 and
    ``chart_read_v2_run.lean``)."""

    windows = [d for d in items if d.part in parts and d.item.presentation == "chunk_cut"]
    groups = message_groups(
        {"request_id": d.item.item_id, "messages": cr.render(d.item)} for d in windows
    )
    return [group[0] for group in groups]


def _said(
    items: Sequence[DraftItem], parts: frozenset[str], tested: str, reference: str, letters: Letters
) -> Units:
    pairs: dict[str, Unit] = {}
    missing = unparsed = 0
    for message in _cut_messages(items, parts):
        if message not in letters[tested] or message not in letters[reference]:
            missing += 1
            continue
        a, b = letters[tested][message], letters[reference][message]
        unparsed += (a is None) + (b is None)
        pairs[message] = (_said_present(tested, a), _said_present(reference, b))
    return Units(pairs, missing, unparsed)


def third_option_units(items: Mapping[str, Sequence[DraftItem]], letters: Letters) -> Units:
    """H5: Part C's cut windows, "present" under the binary prompt against the three-option one."""

    return _said(items["binary"], frozenset({"cut"}), "binary", "abstain", letters)


def order_units(items: Mapping[str, Sequence[DraftItem]], letters: Letters) -> Units:
    """H6: v1's and Part C's cut windows, "present" under the binary prompt against the swapped
    one."""

    return _said(items["binary"], frozenset({run.V1_CUT, "cut"}), "binary", "swap", letters)


def medication_units(items: Mapping[str, Sequence[DraftItem]], letters: Letters) -> Units:
    """H7: each Part D pair but ``med_held``, the not-taking twin's error against the taking
    twin's."""

    twins: dict[str, dict[bool, DraftItem]] = defaultdict(dict)
    for d in items["med"]:
        if d.item.construction != "med_held":
            twins[d.item.pair][d.item.status is Status.PRESENT] = d
    tested = {pair: t[False] for pair, t in twins.items()}
    reference = {t[True].item.item_id: t[True] for t in twins.values()}
    pairing = {pair: t[True].item.item_id for pair, t in twins.items()}
    return _paired_errors(tested, reference, pairing, "med", letters)


TESTS: Final[tuple[Test, ...]] = (
    Test(
        "H1",
        "errors at 6,000 words of filler exceed errors at 750",
        GREATER,
        ("binary",),
        depth_units,
    ),
    Test(
        "H2",
        'FDR on "history of {chronic}" exceeds FDR on "{X}, active problem."',
        GREATER,
        ("binary",),
        _template_units(
            ("history_chronic.a0", "history_chronic.a1", "history_chronic.a2"),
            "history_chronic.ref",
        ),
    ),
    Test(
        "H3",
        "FDR on a chronic disease is higher when its flare is over than when active",
        GREATER,
        ("binary",),
        _template_units(("flare_chronic.b",), "flare_chronic.a"),
    ),
    Test(
        "H4",
        "FDR under PAST MEDICAL HISTORY exceeds FDR under ACTIVE PROBLEMS",
        GREATER,
        ("binary",),
        _template_units(("pmh_chronic.a",), "pmh_chronic.ref"),
    ),
    Test(
        "H5",
        'on cut windows "present" is less frequent under three options than under two',
        GREATER,
        ("binary", "abstain"),
        third_option_units,
    ),
    Test(
        "H6",
        'on cut windows the share saying "present" differs between the two orders',
        TWO_SIDED,
        ("binary", "swap"),
        order_units,
    ),
    Test(
        "H7",
        "pairs with only the not-taking twin wrong outnumber the reverse",
        GREATER,
        ("med",),
        medication_units,
    ),
)


# --- reading a run ----------------------------------------------------------------------------


def _items(directory: Path) -> dict[str, list[DraftItem]]:
    return {name: run.load(directory / name / "pass1") for name in run.PROMPTS}


def _letters(directory: Path, arm: str) -> dict[str, list[dict[str, str | None]]]:
    """Every finished pass of every prompt ``arm`` answered, through the descriptive reader."""

    return {name: run.read_passes(prompt, directory, arm) for name, prompt in run.PROMPTS.items()}


def _counts(units: Units, direction: str) -> dict[str, Any]:
    tested = sum(a and not b for a, b in units.pairs.values())
    reference = sum(b and not a for a, b in units.pairs.values())
    return {
        "units": len(units.pairs),
        "tested_only": tested,
        "reference_only": reference,
        "both": sum(a and b for a, b in units.pairs.values()),
        "missing": units.missing,
        "unparsed": units.unparsed,
        "p": p_value(direction, tested, reference),
    }


def _pass_units(
    test: Test,
    items: Mapping[str, Sequence[DraftItem]],
    letters: Mapping[str, Sequence[Mapping[str, str | None]]],
    k: int,
) -> Units | None:
    """``test``'s units on pass ``k``, or ``None`` where the arm has not finished that pass of
    every prompt the test reads."""

    if any(len(letters[name]) < k for name in test.prompts):
        return None
    return test.units(items, {name: letters[name][k - 1] for name in test.prompts})


def _pooled(test: Test, per_arm: Mapping[str, Units]) -> dict[str, Any]:
    tested = sum(sum(a and not b for a, b in u.pairs.values()) for u in per_arm.values())
    reference = sum(sum(b and not a for a, b in u.pairs.values()) for u in per_arm.values())
    clusters: dict[str, list[Unit]] = defaultdict(list)
    for units in per_arm.values():
        for key, unit in units.pairs.items():
            clusters[key].append(unit)
    return {
        "arms": sorted(per_arm),
        "tested_only": tested,
        "reference_only": reference,
        "p": p_value(test.direction, tested, reference),
        "clustered": clustered_paired(clusters),
    }


def order_leans(units_by_arm: Mapping[str, Units], letters_by_arm: Mapping[str, Letters]) -> Any:
    """H6's two estimates, per arm and pooled: the share of answers over both orders that say
    "present", and the share that choose the first letter. Each answer is one of two on its
    message, so the interval resamples messages."""

    def shares(arm: str, message: str) -> tuple[float, float]:
        binary, swapped = (
            letters_by_arm[arm]["binary"][message],
            letters_by_arm[arm]["swap"][message],
        )
        present = _said_present("binary", binary) + _said_present("swap", swapped)
        first = (binary == "A") + (swapped == "A")
        return present / 2, first / 2

    def estimate(rows: Sequence[tuple[float, float]]) -> dict[str, Any]:
        out: dict[str, Any] = {"messages": len(rows)}
        rng = random.Random(BOOTSTRAP_SEED)
        for index, name in ((0, "presence_lean"), (1, "first_option_lean")):
            values = [row[index] for row in rows]
            if not values:
                out[name] = None
                continue
            draws = sorted(
                sum(values[rng.randrange(len(values))] for _ in values) / len(values)
                for _ in range(BOOTSTRAP_RESAMPLES)
            )
            out[name] = {
                "estimate": round(sum(values) / len(values), 4),
                "ci95": [
                    round(draws[int(0.025 * (len(draws) - 1))], 4),
                    round(draws[math.ceil(0.975 * (len(draws) - 1))], 4),
                ],
            }
        return out

    rows = {arm: [shares(arm, m) for m in sorted(u.pairs)] for arm, u in units_by_arm.items()}
    return {
        "arms": {arm: estimate(r) for arm, r in rows.items()},
        "pooled": estimate([row for r in rows.values() for row in r]),
    }


def report(
    directory: Path, arms: Sequence[str], reference_arms: Sequence[str] = ()
) -> dict[str, Any]:
    """The seven tests on the run under ``directory``: ``arms`` are pooled, ``reference_arms``
    (Layer 2) reported beside them. A test an arm has not finished is left out for that arm,
    and the arms a pooled test stands on are named."""

    text = PREREGISTRATION.read_bytes() if PREREGISTRATION.exists() else b""
    items = _items(directory)
    letters = {arm: _letters(directory, arm) for arm in [*arms, *reference_arms]}
    tests: dict[str, Any] = {}
    pooled_p: dict[str, float] = {}
    for test in TESTS:
        node: dict[str, Any] = {
            "claim": test.claim,
            "direction": test.direction,
            "prompts": list(test.prompts),
        }
        passes = max(run.PROMPTS[name].passes for name in test.prompts)
        for k in range(1, passes + 1):
            per_arm = {
                arm: units
                for arm in arms
                if (units := _pass_units(test, items, letters[arm], k)) is not None
            }
            label = "pass1" if k == 1 else f"sensitivity_pass{k}"
            node[label] = {
                "arms": {arm: _counts(units, test.direction) for arm, units in per_arm.items()},
                "pooled": _pooled(test, per_arm) if per_arm else None,
            }
            if k == 1:
                node["reference_arms"] = {
                    arm: _counts(units, test.direction)
                    for arm in reference_arms
                    if (units := _pass_units(test, items, letters[arm], 1)) is not None
                }
                if per_arm:
                    pooled_p[test.name] = node[label]["pooled"]["p"]
                if test.name == "H6" and per_arm:
                    node["estimates"] = order_leans(
                        per_arm,
                        {arm: {n: letters[arm][n][0] for n in test.prompts} for arm in per_arm},
                    )
        tests[test.name] = node
    return {
        "preregistration": "docs/reference/chartread-v2-preregistration.md",
        "registered_sha256": PREREGISTRATION_SHA256,
        "matches_registration": hashlib.sha256(text).hexdigest() == PREREGISTRATION_SHA256,
        "arms": list(arms),
        "reference_arms": list(reference_arms),
        "tests": tests,
        "holm": cr.holm(pooled_p),
        "holm_over": sorted(pooled_p),
    }


# --- which Layer 2 answered -------------------------------------------------------------------


def source_tree(package: Path) -> str:
    """The committed tree ``package`` was imported from, refused while it differs from that
    commit: an answer is attributed to a tree id, so uncommitted code would answer under a name
    that is not its own. Asked of the directory the code was actually imported from, because a
    worktree and an editable install can each put a different copy first."""

    def git(*args: str) -> str:
        done = subprocess.run(
            ["git", "-C", str(package), *args], capture_output=True, text=True, check=True
        )
        return done.stdout.strip()

    top = Path(git("rev-parse", "--show-toplevel"))
    relative = package.resolve().relative_to(top.resolve()).as_posix()
    if git("status", "--porcelain", "--", "."):
        raise ValueError(f"{relative} has uncommitted changes in {top}: commit or use a worktree")
    return git("rev-parse", f"HEAD:{relative}")
