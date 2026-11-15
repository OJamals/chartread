"""ChartRead addendum 4: what the cut-window error is worth to a patient, without chart data.

Three parts, pre-registered in ``docs/reference/chartread-addendum-4-clinical.md``:

* **reading** — clinicians answer the cut windows the models answered, under the models' own
  three options, so a window that does not say can be told apart from a model that over-reads it;
* **harm** — clinicians rate the most likely harm of each wrong reading, per finding and status,
  apart from any model's answers, so an error count becomes a count of errors that matter;
* **burden** — how many findings real notes leave to a header (a family-history statement that
  names no relative) or to a cue earlier in a list ("Negative for fever, chills, or night
  sweats"), how often a chunker separates them from it, and what that comes to per 100 notes at
  the false affirmation rates already measured.

Nothing here opens a corpus or calls a model; the script does. ``read_answers`` reads a filled
sheet whose answers are not v1's A or B.
"""

from __future__ import annotations

import csv
import random
import re
from collections import Counter
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

from clinical_eval.chart_read import (
    COMPATIBLE,
    CONCEPTS,
    ChartItem,
    Rate,
    Status,
    cohen_kappa,
)
from clinical_eval.chart_read_notes import (
    CONTROL,
    FH_BARE,
    HEADER,
    Exposure,
    Unit,
    findings,
    sections,
    statements,
)
from clinical_eval.scoring import bootstrap_interval, mcnemar_exact

SEED: Final = 20261019

# --- burden: findings a header or a leading cue owns ------------------------------------------

NEG_LIST: Final = "neg_list"
#: Forward negation triggers a review of systems is written with (ConText's, less those that do
#: not open a list). The first one in a statement opens the list.
LIST_CUE: Final = re.compile(
    r"\b(?:negative for|no evidence of|no history of|no signs? of|no symptoms of|denies|denied|"
    r"denying|deny|without|free of|absence of|no)\b",
    re.IGNORECASE,
)
#: Where a forward negation stops reaching: ConText's terminations, a positive cue, a clause that
#: starts a new assertion, and a colon, which opens another label inside the statement.
LIST_STOP: Final = re.compile(
    r"\b(?:but|however|except|although|though|aside from|apart from|other than|positive for|"
    r"reports?|reported|endorses?|endorsed|admits?|admitted|complains?|complained|which|who|"
    r"secondary to|due to|because|since|this|that|appears?|appeared|consistent with|"
    r"suggest\w*|there (?:is|are|was|were)|past history|presents?|presented)\b|:",
    re.IGNORECASE,
)
#: A negation word that does not negate what follows it (ConText's pseudo-triggers): "no change
#: in the aneurysm" says the aneurysm is there.
PSEUDO_CUE: Final = re.compile(
    r"\s+(?:significant |interval |further |obvious |appreciable |substantial )?"
    r"(?:change|increase|decrease|improvement|worsening|growth|progression|reduction)\b",
    re.IGNORECASE,
)
#: A list finding's place in its statement, folded into ``Unit.index`` so its id is unique.
_LIST_SLOT: Final = 1000


def negated_lists(
    note: str,
    text: str,
    form: str,
    lexicon: Mapping[str, str],
    census: Counter[str],
    header: re.Pattern[str] = HEADER,
) -> list[Unit]:
    """Every lexicon finding a leading negation cue owns, one unit per finding.

    A unit's span is the finding's own words and its one governor is the cue: a chunk that holds
    the finding without the cue reads "chills, or night sweats" with nothing to say they were
    denied. Statements are those of headed sections; text before the first header is not read.
    """

    out = []
    for s_index, section in enumerate(sections(text, header)):
        for t_index, span in enumerate(statements(text, section)):
            cue = LIST_CUE.search(text, span[0], span[1])
            while cue is not None and PSEUDO_CUE.match(text, cue.end()):
                census["pseudo_cues"] += 1
                cue = LIST_CUE.search(text, cue.end(), span[1])
            if cue is None:
                continue
            stop = LIST_STOP.search(text, cue.end(), span[1])
            reach = (cue.end(), stop.start() if stop else span[1])
            found = findings(text, reach, lexicon)
            census["statements_with_cue"] += 1
            census["findings_owned_by_cue"] += len(found)
            if stop and findings(text, (stop.start(), span[1]), lexicon):
                census["statements_with_findings_past_a_stop"] += 1
            for k, (start, end, cui) in enumerate(found):
                out.append(
                    Unit(
                        note=note,
                        form=form,
                        kind=NEG_LIST,
                        header=section.header,
                        span=(start, end),
                        header_span=section.header_span,
                        governors=(cue.span(),),
                        findings=((start, end, cui),),
                        index=(s_index, t_index * _LIST_SLOT + k),
                    )
                )
    return out


def burden(
    pairs: Iterable[tuple[Unit, Exposure]],
    notes: int,
    count: Callable[[Unit], int] = lambda unit: len(unit.findings),
) -> dict[str, Any]:
    """Findings a chunking leaves without their owner, in three readings of "a reader sees it".

    ``low`` counts a finding only when every chunk holding it exposes it, ``high`` when any one
    does, and ``mid`` weights it by the share of the chunks holding it that expose it — what a
    retriever returning one of them at random would hand over. ``count`` is how many findings a
    unit carries (all of them by default; the harm reading counts only the serious ones).
    """

    total = low = mid = high = 0.0
    for unit, seen in pairs:
        n = count(unit)
        total += n
        if seen.containing:
            mid += n * seen.exposing / seen.containing
            low += n if seen.exposing == seen.containing else 0
        high += n if seen.exposing else 0
    return {
        "notes": notes,
        "findings": total,
        "findings_per_100_notes": round(100 * total / notes, 3) if notes else None,
        "exposed": {"low": low, "mid": round(mid, 3), "high": high},
    }


def per_hundred(owned: Mapping[str, Any], far: Mapping[str, Any]) -> dict[str, Any]:
    """Expected false assertions per 100 notes: exposed findings times the false affirmation rate.

    The point uses ``mid`` exposure and the measured rate. ``range`` takes the rate's Wilson
    interval with the ``low`` and ``high`` exposures, so it carries both uncertainties at once.
    """

    notes = int(owned["notes"])
    rate, (rate_low, rate_high) = float(far["rate"]), far["ci95"]
    exposed = owned["exposed"]

    def scale(findings_exposed: float, p: float) -> float:
        return round(100 * findings_exposed * float(p) / notes, 3)

    return {
        "point": scale(exposed["mid"], rate),
        "range": [scale(exposed["low"], rate_low), scale(exposed["high"], rate_high)],
        "far": rate,
    }


# --- reading: the clinician baseline ----------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ReadingItem:
    """One window a clinician reads, and every model item whose text it is.

    A constructed cut window is the same text for both twins — the header was the only
    difference — so one answer stands for both.
    """

    ref: str
    text: str
    finding: str
    source: str
    item_ids: tuple[str, ...]


CONSTRUCTED: Final = "constructed"
REAL_FAMILY: Final = "mtsamples_family"
REAL_CONTROL: Final = "mtsamples_control"


def reading_items(
    constructed: Iterable[ChartItem], real: Iterable[ChartItem], seed: int = SEED
) -> list[ReadingItem]:
    """The section construction's cut windows, one per twin pair, and addendum 2's clean bare
    family and control cut windows; shuffled with ``seed`` and numbered in that order."""

    twins: dict[str, list[ChartItem]] = {}
    for item in constructed:
        if item.construction == "section" and item.presentation == "chunk_cut":
            twins.setdefault(item.pair, []).append(item)
    windows: list[tuple[str, str, str, tuple[str, ...]]] = []
    for pair in sorted(twins):
        group = twins[pair]
        if len({(i.text, i.concept) for i in group}) != 1:
            raise ValueError(f"{pair}: twins' cut windows differ, so one answer cannot stand")
        ids = tuple(sorted(i.item_id for i in group))
        windows.append((group[0].text, group[0].concept, CONSTRUCTED, ids))
    for item in sorted(real, key=lambda i: i.item_id):
        if item.presentation != "cut" or item.construction not in (FH_BARE, CONTROL):
            continue
        source = REAL_FAMILY if item.construction == FH_BARE else REAL_CONTROL
        windows.append((item.text, item.concept, source, (item.item_id,)))
    random.Random(seed).shuffle(windows)
    return [
        ReadingItem(f"R{n:03d}", text, finding, source, ids)
        for n, (text, finding, source, ids) in enumerate(windows, start=1)
    ]


def _shares(letters: Sequence[str | None]) -> dict[str, Any]:
    n = len(letters)
    return {
        letter: Rate(hits=sum(x == letter for x in letters), n=n, unanswered=0).as_dict()
        for letter in ("A", "B", "C")
    } | {"unanswered": sum(x is None for x in letters), "n": n}


def reading_scorecard(
    windows: Sequence[ReadingItem], raters: Mapping[str, Mapping[str, tuple[str | None, str]]]
) -> dict[str, Any]:
    """Each rater's, and all raters' pooled, shares of A, B and C per source, and kappa."""

    report: dict[str, Any] = {"raters": {}, "pooled": {}}
    for source in (CONSTRUCTED, REAL_FAMILY, REAL_CONTROL):
        group = [w for w in windows if w.source == source]
        pooled: list[str | None] = []
        for name, ratings in raters.items():
            given = [ratings.get(w.ref, (None, ""))[0] or None for w in group]
            report["raters"].setdefault(name, {})[source] = _shares(given)
            pooled.extend(given)
        report["pooled"][source] = _shares(pooled)
    names = list(raters)
    if len(names) >= 2:
        first, second = raters[names[0]], raters[names[1]]
        shared = [
            w.ref for w in windows if first.get(w.ref, (None,))[0] and second.get(w.ref, (None,))[0]
        ]
        kappa = cohen_kappa([str(first[r][0]) for r in shared], [str(second[r][0]) for r in shared])
        report["kappa"] = None if kappa is None else round(kappa, 4)
        report["kappa_shared"] = len(shared)
    return report


def non_present_id(window: ReadingItem, items: Mapping[str, ChartItem]) -> str | None:
    """The item a false affirmation on this window is counted on: the twin that is not present."""

    for item_id in window.item_ids:
        if items[item_id].status is not Status.PRESENT:
            return item_id
    return None


def reading_test(
    windows: Sequence[ReadingItem],
    items: Mapping[str, ChartItem],
    raters: Mapping[str, Mapping[str, tuple[str | None, str]]],
    model_letters: Mapping[str, str | None],
) -> dict[str, Any]:
    """RB1 for one arm: on non-present cut windows, does the model affirm where clinicians do not?

    One pair per rater and window, the model's letter on the window's non-present item against
    the rater's; discordant pairs counted both ways; exact one-sided McNemar. A window either side
    left unanswered is not a pair.
    """

    model_only = rater_only = pairs = 0
    for window in windows:
        target = non_present_id(window, items)
        if target is None:
            continue
        model = model_letters.get(target)
        if model is None:
            continue
        for ratings in raters.values():
            human = ratings.get(window.ref, (None, ""))[0]
            if not human:
                continue
            pairs += 1
            model_only += model == "A" and human != "A"
            rater_only += human == "A" and model != "A"
    return {
        "pairs": pairs,
        "model_only": model_only,
        "rater_only": rater_only,
        "p": mcnemar_exact(model_only, rater_only),
    }


# --- harm -------------------------------------------------------------------------------------

AFFIRMED: Final = "affirmed"
DENIED: Final = "denied"
#: What a row tells the rater the note said, by the status the note gave the finding.
SAID: Final[dict[Status, str]] = {
    Status.NEGATED: "Listed as current. The note said: denied or ruled out.",
    Status.POSSIBLE: "Listed as current. The note said: only suspected or possible.",
    Status.HISTORICAL: "Listed as current. The note said: in the past, fully resolved.",
    Status.FAMILY: "Listed as current. The note said: a relative has it; nothing says the "
    "patient does.",
    Status.HYPOTHETICAL: "Listed as current. The note said: only a future possibility (a "
    "warning sign or a risk).",
    Status.PRESENT: "Listed as absent. The note said: the patient has it now.",
}
HARM_SCORE: Final = {"H0": 0, "H1": 1, "H2": 2, "H3": 3, "H4": 4}
#: The mean level at or above which a wrong reading is counted serious: moderate harm or worse.
SERIOUS: Final = 2.0


@dataclass(frozen=True, slots=True)
class HarmRow:
    """One wrong reading of one finding; ``cui`` is set for a real note's family finding."""

    ref: str
    finding: str
    status: Status
    error: str
    cui: str = ""

    @property
    def text(self) -> str:
        return SAID[self.status]


def harm_key(error: str, status: Status, finding: str) -> str:
    return f"{error}|{status.value}|{finding.lower()}"


def harm_rows(real_family: Iterable[tuple[str, str]], seed: int = SEED) -> list[HarmRow]:
    """Every wrong reading v1's constructed items can carry, then each real family finding no
    constructed concept already names, as ``(finding, cui)``.

    For a constructed concept: each status it is generated in that is not present, as a false
    affirmation, and its present status as a false denial. Findings are shuffled with ``seed``;
    one finding's rows stay together, so a rater sets its levels side by side.
    """

    groups: list[list[tuple[str, Status, str, str]]] = []
    named = set()
    for kind, concepts in CONCEPTS.items():
        for concept in concepts:
            named.add(concept.lower())
            group: list[tuple[str, Status, str, str]] = [
                (concept, status, AFFIRMED, "")
                for status in COMPATIBLE[kind]
                if status is not Status.PRESENT
            ]
            group.append((concept, Status.PRESENT, DENIED, ""))
            groups.append(group)
    seen: set[str] = set()
    for finding, cui in real_family:
        if finding.lower() in named or cui in seen:
            continue
        seen.add(cui)
        groups.append([(finding, Status.FAMILY, AFFIRMED, cui)])
    random.Random(seed).shuffle(groups)
    rows = [row for group in groups for row in group]
    return [
        HarmRow(f"H{n:03d}", finding, status, error, cui)
        for n, (finding, status, error, cui) in enumerate(rows, start=1)
    ]


def weighted_kappa(first: Sequence[int], second: Sequence[int], levels: int = 5) -> float | None:
    """Cohen's kappa with quadratic weights, for ordered levels ``0 .. levels - 1``."""

    n = len(first)
    if n == 0 or n != len(second):
        return None
    observed = [[0.0] * levels for _ in range(levels)]
    for a, b in zip(first, second, strict=True):
        observed[a][b] += 1 / n
    row = [sum(observed[i]) for i in range(levels)]
    col = [sum(observed[i][j] for i in range(levels)) for j in range(levels)]
    weight = [[(i - j) ** 2 / (levels - 1) ** 2 for j in range(levels)] for i in range(levels)]
    disagree = sum(weight[i][j] * observed[i][j] for i in range(levels) for j in range(levels))
    chance = sum(weight[i][j] * row[i] * col[j] for i in range(levels) for j in range(levels))
    return None if chance == 0 else 1 - disagree / chance


def harm_weights(
    rows: Sequence[HarmRow], raters: Mapping[str, Mapping[str, tuple[str | None, str]]]
) -> dict[str, Any]:
    """Each row's mean level over the raters who answered it, agreement, and the rows two raters
    set two or more levels apart."""

    means: dict[str, float] = {}
    apart: list[dict[str, Any]] = []
    for row in rows:
        levels = [
            HARM_SCORE[answer]
            for answer, _ in (r.get(row.ref, (None, "")) for r in raters.values())
            if answer in HARM_SCORE
        ]
        if levels:
            means[row.ref] = sum(levels) / len(levels)
        if len(levels) >= 2 and max(levels) - min(levels) >= 2:
            apart.append(
                {
                    "ref": row.ref,
                    "finding": row.finding,
                    "status": row.status.value,
                    "levels": levels,
                }
            )
    report: dict[str, Any] = {"rows": len(rows), "rated": len(means), "apart_by_two": apart}
    names = list(raters)
    if len(names) >= 2:
        pairs = [
            (HARM_SCORE[a], HARM_SCORE[b])
            for row in rows
            if (a := raters[names[0]].get(row.ref, (None, ""))[0]) in HARM_SCORE
            and (b := raters[names[1]].get(row.ref, (None, ""))[0]) in HARM_SCORE
        ]
        first, second = [a for a, _ in pairs], [b for _, b in pairs]
        kappa = weighted_kappa(first, second)
        interval = bootstrap_interval(first, second, weighted_kappa)
        report["quadratic_kappa"] = None if kappa is None else round(kappa, 4)
        report["quadratic_kappa_ci95"] = (
            None if interval is None else [round(v, 4) for v in interval]
        )
        report["shared"] = len(pairs)
    rounded = Counter(round(m) for m in means.values())
    report["levels"] = {str(level): rounded[level] for level in sorted(rounded)}
    report["means"] = means
    return report


def weight_lookup(
    rows: Sequence[HarmRow], means: Mapping[str, float]
) -> Callable[[ChartItem, str], float | None]:
    """The mean harm of an item's wrong reading: a constructed item by its concept, a real one by
    the CUI in its ``detail`` or, failing that, its concept."""

    by_name: dict[str, float] = {}
    by_cui: dict[str, float] = {}
    for row in rows:
        if row.ref not in means:
            continue
        by_name[harm_key(row.error, row.status, row.finding)] = means[row.ref]
        if row.cui:
            by_cui[harm_key(row.error, row.status, row.cui)] = means[row.ref]

    def weight(item: ChartItem, error: str) -> float | None:
        status = item.status if error == AFFIRMED else Status.PRESENT
        if item.detail and (hit := by_cui.get(harm_key(error, status, item.detail))) is not None:
            return hit
        return by_name.get(harm_key(error, status, item.concept))

    return weight


def harm_errors(
    items: Iterable[ChartItem],
    letters: Mapping[str, str | None],
    weight: Callable[[ChartItem, str], float | None],
) -> dict[str, Any]:
    """False affirmations and false denials with their harm: how many, how many have a rating,
    their mean level, and serious ones per 100 items that could carry the error."""

    report: dict[str, Any] = {}
    population = list(items)
    for error, wrong, at_risk in (
        (AFFIRMED, "A", [i for i in population if i.status is not Status.PRESENT]),
        (DENIED, "B", [i for i in population if i.status is Status.PRESENT]),
    ):
        made = [i for i in at_risk if letters.get(i.item_id) == wrong]
        rated = [w for w in (weight(i, error) for i in made) if w is not None]
        serious = sum(w >= SERIOUS for w in rated)
        report[error] = {
            "items": len(at_risk),
            "errors": len(made),
            "rated": len(rated),
            "mean_harm": round(sum(rated) / len(rated), 3) if rated else None,
            "serious": serious,
            "serious_per_100": round(100 * serious / len(at_risk), 2) if at_risk else None,
        }
    return report


def read_answers(path: Path, letters: Sequence[str]) -> dict[str, tuple[str | None, str]]:
    """``ref -> (answer, flag)`` from a filled sheet; an answer outside ``letters`` is ``None``."""

    ratings: dict[str, tuple[str | None, str]] = {}
    with path.open(newline="", encoding="utf-8-sig") as handle:
        for row in csv.DictReader(handle):
            answer = (row.get("answer") or "").strip().upper()
            flag = (row.get("flag") or "").strip().upper()
            ratings[row["ref"].strip()] = (answer if answer in letters else None, flag)
    return ratings
