"""ChartRead addendum 4: how many findings a chunking leaves without what says whose they are.

Pre-registration: ``docs/chartread-addendum-4-clinical.md``. A **unit** is a statement whose
findings only something else in the note governs: a family-history statement that names no
relative, governed by its section header, or a finding in a negated list ("denies fever, chills,
or night sweats"), governed by the leading cue. A chunk **exposes** a unit when it holds the
unit's statement and none of its governors. The burden of a chunking is how many findings it
leaves exposed, per 100 notes, and that times a model's measured false affirmation rate is the
expected number of false assertions per 100 notes.

This module is the half that needs no lexicon: given where each unit's statement and governors
sit (character offsets) and where a chunker cut the note, it counts exposure, burden and the
per-100-note estimates. ``data/addendum4-impact`` carries the units and chunks of MTSamples and
ACI-Bench as offsets only, so every exposure and per-100-note figure the addendum reports can be
recomputed here. The half that *finds* units in a note is not released: it matches a disorder
lexicon drawn from the UMLS Metathesaurus, which needs a licence. A pipeline that locates its own
findings and their governors — with any lexicon — can measure its own exposure with this module
and its own chunker's boundaries, without a model.

The harm-weighted cells (``serious_*``) also need the finding's words and the clinicians' harm
ratings, neither of which is released; ``report_burden.json`` carries them as counts.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Final

from chartread.core import Rate

NEG_LIST: Final = "neg_list"
FH_BARE: Final = "fh_bare"
FH_ANAPHORIC: Final = "fh_anaphoric"
FAMILY_KINDS: Final = frozenset({FH_BARE, FH_ANAPHORIC})
FAMILY: Final = "family"

#: The arms whose false affirmation rates the addendum scales exposure by.
ARMS: Final = ("terra", "grok47", "deepseekv4pro", "qwen38max", "glm53", "minimaxm3", "musespark13")


@dataclass(frozen=True, slots=True)
class Unit:
    """One unit as offsets into its note: the statement, what governs it, and its findings."""

    unit_id: str
    note: str
    form: str
    kind: str
    span: tuple[int, int]
    governors: tuple[tuple[int, int], ...]
    findings: tuple[tuple[int, int], ...]

    @classmethod
    def from_row(cls, row: Mapping[str, Any]) -> Unit:
        return cls(
            unit_id=str(row["unit_id"]),
            note=str(row["note"]),
            form=str(row["form"]),
            kind=str(row["kind"]),
            span=(int(row["span"][0]), int(row["span"][1])),
            governors=tuple((int(g[0]), int(g[1])) for g in row["governors"]),
            findings=tuple((int(f[0]), int(f[1])) for f in row.get("findings", ())),
        )

    @property
    def group(self) -> str:
        """``family`` for either family kind, else the kind itself."""

        return FAMILY if self.kind in FAMILY_KINDS else self.kind


def _within(inner: tuple[int, int], outer: tuple[int, int]) -> bool:
    return outer[0] <= inner[0] and inner[1] <= outer[1]


@dataclass(frozen=True, slots=True)
class Exposure:
    """One chunking of one unit: the chunks that hold it, and those of them that expose it."""

    containing: int
    exposing: int

    @property
    def split(self) -> bool:
        """No chunk holds the whole statement — a retriever cannot return it intact."""

        return self.containing == 0


def exposure(unit: Unit, chunks: Sequence[tuple[int, int]]) -> Exposure:
    holding = [c for c in chunks if _within(unit.span, c)]
    exposing = [c for c in holding if not any(_within(g, c) for g in unit.governors)]
    return Exposure(containing=len(holding), exposing=len(exposing))


def exposure_rates(pairs: Iterable[tuple[Unit, Exposure]]) -> dict[str, Any]:
    """``any``: some chunk holding the statement lacks every governor. ``all``: every one does."""

    population = list(pairs)
    n = len(population)
    return {
        "units": n,
        "any": Rate(hits=sum(e.exposing > 0 for _, e in population), n=n, unanswered=0).as_dict(),
        "all": Rate(
            hits=sum(e.containing > 0 and e.exposing == e.containing for _, e in population),
            n=n,
            unanswered=0,
        ).as_dict(),
        "split": Rate(hits=sum(e.split for _, e in population), n=n, unanswered=0).as_dict(),
    }


def burden(
    pairs: Iterable[tuple[Unit, Exposure]],
    notes: int,
    count: Callable[[Unit], int] = lambda unit: len(unit.findings),
) -> dict[str, Any]:
    """Findings a chunking leaves without their owner, in three readings of "a reader sees it".

    ``low`` counts a finding only when every chunk holding it exposes it, ``high`` when any one
    does, and ``mid`` weights it by the share of the chunks holding it that expose it — what a
    retriever returning one of them at random would hand over. ``count`` is how many findings a
    unit carries (all of them by default).
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


def corpus(note: str) -> str:
    return note.split("/", 1)[0]


def cells(
    units: Iterable[Unit],
    chunk_rows: Iterable[Mapping[str, Any]],
    notes_by_corpus: Mapping[str, int],
    rates: Mapping[str, Mapping[str, Mapping[str, Any]]],
) -> dict[str, Any]:
    """Every (corpus, form, chunker, unit group) cell: exposure, burden and per 100 notes.

    ``chunk_rows`` are ``{"note", "form", "setting", "chunks"}``; ``rates`` maps each arm to its
    false affirmation rate by unit group (``family``, ``neg_list``), as ``Rate.as_dict`` writes it.
    """

    by_note: dict[tuple[str, str], list[Unit]] = defaultdict(list)
    for unit in units:
        by_note[(unit.note, unit.form)].append(unit)
    grouped: dict[tuple[str, str, str, str], list[tuple[Unit, Exposure]]] = defaultdict(list)
    for row in chunk_rows:
        chunks = [(int(c[0]), int(c[1])) for c in row["chunks"]]
        for unit in by_note.get((str(row["note"]), str(row["form"])), []):
            key = (corpus(unit.note), unit.form, str(row["setting"]), unit.group)
            grouped[key].append((unit, exposure(unit, chunks)))
    out: dict[str, Any] = {}
    for (name, form, setting, group), pairs in sorted(grouped.items()):
        owned = burden(pairs, notes_by_corpus[name])
        out.setdefault(name, {}).setdefault(form, {}).setdefault(setting, {})[group] = {
            "exposure": exposure_rates(pairs),
            "notes_exposed": len({u.note for u, e in pairs if e.exposing > 0}),
            "owned": owned,
            "per_100_notes": {arm: per_hundred(owned, rates[arm][group]) for arm in rates},
        }
    return out
