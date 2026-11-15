"""Which authored questions are held out for evaluation, and whether a text is one of them.

**The rule is a bank family, not a sample.** Every record of every ``Clinical Form`` bank is held
out; every ``Question Bank`` record stays available for training. Chosen by measurement over
``sources/medical_mcq`` on 2026-09-29, not by preference:

- The forms are full-length, exam-style clinical vignettes keyed by the publisher's own printed
  answer lines (``publisher_item_key``, ``publisher_item_key_vision``) — the physician-level
  material the goal is about. 1,585 of their 1,588 records pass admission; 30 banks, eight
  specialties.
- A family is a product line. Holding out whole families rather than items or single banks is
  what makes leakage structurally unlikely, and it was measured absent: no held-out stem has an
  exact duplicate among the Question Bank stems, and two share half their 8-grams with one —
  both generic continuation lines ("Which of the following is the most likely diagnosis?").
- It is 5.2% of the corpus, below the 10% plan 009 pencilled in. Adding Question Bank banks to
  reach 10% would add recall-style items (anatomy, forensic medicine, PSM), not physician-level
  ones, and would cost training data for no gain in what the evaluation measures.

The rule lives in the builder (:func:`clinical_dataset_builder.medical_mcq.is_held_out`), because
its intake is where the rule is enforced; this module re-exports it and owns what is measured
about it. A held-out record is never training material. :class:`HeldOutIndex` is what an exporter
asks;
the frozen manifest (``docs/reference/eval-split-v1.json``) is what makes the split
reproducible and checkable without the corpus — it carries ids and stem digests, never text.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter, defaultdict
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final

from clinical_dataset_builder.medical_mcq import (
    EVAL_SPLIT_VERSION,
    HELD_OUT_FAMILIES,
)
from clinical_dataset_builder.medical_mcq import is_held_out as is_held_out  # re-exported

SPLIT_VERSION: Final = EVAL_SPLIT_VERSION
HELD_OUT_RULE: Final = "every record whose source names a held-out bank family: " + ", ".join(
    repr(family) for family in HELD_OUT_FAMILIES
)
#: 8-grams, the unit the leakage measurement above used. Long enough that two unrelated stems do
#: not share one by chance; short enough that a lightly edited copy still shares most of them.
SHINGLE_WORDS: Final = 8
#: A held-out stem is matched as a near-copy only if it has at least this many 8-grams (~27
#: words). Shorter stems — continuation items such as "Which of the following is the most
#: appropriate management?" — are nothing but generic question phrasing: on 2026-09-29 they were
#: fully "contained" in 4 of 100 retrieved textbook passages that shared ~1% of the actual item's
#: stem. 1,541 of the 1,588 held-out stems have 40 or more; 12 have fewer than 20 and are matched
#: exactly instead.
MIN_DISTINCTIVE_SHINGLES: Final = 20

_NON_ALNUM = re.compile(r"[^a-z0-9]+")


def record_key(record: Mapping[str, Any]) -> str:
    """``<source>#<id>``: a record's id alone is not unique in this corpus.

    Measured 2026-09-29: 823 ids name more than one question across banks (simpledoctor-0sm),
    619 of them among the held-out records; the pair of bank and id names each record once.
    """

    source = str(record.get("source") or "").strip()
    record_id = str(record.get("id") or "").strip()
    if not source or not record_id:
        raise ValueError(f"a record needs both a source and an id: {source!r} {record_id!r}")
    return f"{source}#{record_id}"


def normalize(text: str) -> str:
    """Case, punctuation and whitespace removed: what two copies of one stem still share."""

    return _NON_ALNUM.sub(" ", text.lower()).strip()


def stem_digest(text: str) -> str:
    return hashlib.sha256(normalize(text).encode("utf-8")).hexdigest()[:16]


def shingles(text: str, width: int = SHINGLE_WORDS) -> frozenset[str]:
    words = normalize(text).split()
    return frozenset(" ".join(words[i : i + width]) for i in range(len(words) - width + 1))


def corpus_records(corpus_dir: Path) -> Iterator[dict[str, Any]]:
    """Every record of the sharded corpus (one JSON array per bank), in file order."""

    for path in sorted(corpus_dir.glob("*.json")):
        records = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(records, list):
            raise ValueError(f"{path}: expected a JSON array of records")
        yield from records


@dataclass(frozen=True, slots=True)
class SplitManifest:
    """The held-out ids, frozen, with a digest of each stem so drift is detectable."""

    version: str
    rule: str
    digests: Mapping[str, str]
    by_source: Mapping[str, int] = field(default_factory=dict)

    def to_json(self) -> str:
        body = {
            "version": self.version,
            "rule": self.rule,
            "records": len(self.digests),
            "by_source": dict(sorted(self.by_source.items())),
            "digests": dict(sorted(self.digests.items())),
        }
        return json.dumps(body, indent=1, ensure_ascii=False) + "\n"

    @classmethod
    def from_json(cls, text: str) -> SplitManifest:
        body = json.loads(text)
        manifest = cls(
            version=str(body["version"]),
            rule=str(body["rule"]),
            digests={str(k): str(v) for k, v in body["digests"].items()},
            by_source={str(k): int(v) for k, v in body.get("by_source", {}).items()},
        )
        if int(body["records"]) != len(manifest.digests):
            raise ValueError("manifest record count disagrees with its digest table")
        return manifest

    @classmethod
    def load(cls, path: Path) -> SplitManifest:
        return cls.from_json(path.read_text(encoding="utf-8"))


def build_manifest(records: Iterable[Mapping[str, Any]]) -> SplitManifest:
    """The manifest the rule produces over these records. Refuses an id seen twice."""

    digests: dict[str, str] = {}
    by_source: Counter[str] = Counter()
    for record in records:
        if not is_held_out(record):
            continue
        key = record_key(record)
        if key in digests:
            raise ValueError(f"record {key!r} appears twice among held-out records")
        digests[key] = stem_digest(str(record.get("question") or ""))
        by_source[str(record.get("source"))] += 1
    return SplitManifest(SPLIT_VERSION, HELD_OUT_RULE, digests, dict(by_source))


def manifest_drift(frozen: SplitManifest, current: SplitManifest) -> list[str]:
    """Every way the corpus has moved since the split was frozen. Empty means unchanged.

    Three findings, not one count, because each asks for something different: a lost id means an
    evaluation item vanished, an added one means the rule now holds out something it did not, and
    a changed digest means an item's text changed under a frozen id.
    """

    problems: list[str] = []
    if frozen.version != current.version or frozen.rule != current.rule:
        problems.append(f"rule changed: {frozen.version} {frozen.rule!r} -> {current.rule!r}")
    lost = sorted(frozen.digests.keys() - current.digests.keys())
    added = sorted(current.digests.keys() - frozen.digests.keys())
    changed = sorted(
        key
        for key in frozen.digests.keys() & current.digests.keys()
        if frozen.digests[key] != current.digests[key]
    )
    for label, ids in (("lost", lost), ("added", added), ("stem changed", changed)):
        if ids:
            problems.append(
                f"{len(ids)} {label}: {', '.join(ids[:5])}{' ...' if len(ids) > 5 else ''}"
            )
    return problems


class HeldOutIndex:
    """What an exporter asks before a text may become training material.

    ``exact`` needs only the manifest; ``containment`` needs the held-out stems themselves, so it is
    built from the corpus. An exporter uses both: an exact copy is refused outright, and a text that
    shares most of a held-out stem's 8-grams is a near-copy that an exact hash would miss.
    """

    def __init__(
        self,
        digests: Iterable[str],
        stems: Iterable[str] = (),
        *,
        min_shingles: int = MIN_DISTINCTIVE_SHINGLES,
    ) -> None:
        self._digests = frozenset(digests)
        self._postings: dict[str, set[int]] = defaultdict(set)
        self._sizes: list[int] = []
        for stem in stems:
            grams = shingles(stem)
            # A short stem is only matched exactly: its one or two 8-grams are generic question
            # phrasing that every question passage contains (see MIN_DISTINCTIVE_SHINGLES).
            if len(grams) < min_shingles:
                continue
            position = len(self._sizes)
            self._sizes.append(len(grams))
            for gram in grams:
                self._postings[gram].add(position)

    @classmethod
    def from_corpus(cls, corpus_dir: Path) -> HeldOutIndex:
        held = [r for r in corpus_records(corpus_dir) if is_held_out(r)]
        stems = [str(r.get("question") or "") for r in held]
        return cls((stem_digest(s) for s in stems), stems)

    def exact(self, text: str) -> bool:
        return stem_digest(text) in self._digests

    def containment(self, text: str) -> float:
        """The largest share of any one held-out stem's 8-grams that ``text`` contains."""

        hits: Counter[int] = Counter()
        for gram in shingles(text):
            for position in self._postings.get(gram, ()):
                hits[position] += 1
        return max((count / self._sizes[position] for position, count in hits.items()), default=0.0)
