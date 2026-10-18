"""The NegEx/ConText annotation set: where its rows come from and how each becomes a case.

The corpus (Chapman et al. 2001; Harkema et al. 2009) is 2,376 concept-and-sentence pairs from
de-identified discharge summaries, radiology, echocardiography and colonoscopy reports, each
labelled by its authors for negation, temporality and experiencer. Its repository declares no
licence, so this repository ships neither its text nor its labels: :func:`fetch` downloads the
file and :data:`CORPUS_SHA256` is what it must hash to.
"""

from __future__ import annotations

import csv
import hashlib
import re
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Final

CORPUS_URL: Final = (
    "https://raw.githubusercontent.com/chapmanbe/negex/master/"
    "genConText/rsAnnotations-1-120-random.txt"
)
CORPUS_SHA256: Final = "890583d9b36c6dd8268a700d2805d9d987dfb3e3fffd5d43db0bad2aedf25291"
DEFAULT_PATH: Final = Path("corpora/rsAnnotations-1-120-random.txt")

REPORT, _BLANK, CONCEPT, SENTENCE, NEGATION, TEMPORALITY, EXPERIENCER = range(7)

_BOUNDED = r"(?<![A-Za-z0-9]){}(?![A-Za-z0-9])"


@dataclass(frozen=True, slots=True)
class CorpusCase:
    """One annotated mention. ``gold_negated``, ``temporality`` and ``experiencer`` are the corpus
    authors' labels; nothing here may set them."""

    identifier: str
    sentence: str
    concept: str
    start: int
    end: int
    gold_negated: bool
    temporality: str
    experiencer: str
    #: How ``start`` was located: ``upper_boundary``, ``boundary``, ``upper_anywhere`` or
    #: ``anywhere``, strongest first (:func:`locate_concept`).
    anchor: str = ""


def locate_concept(sentence: str, concept: str) -> tuple[int, str] | None:
    """Where the annotated concept sits in its sentence, and how that was decided.

    The corpus upper-cases the annotated concept inside its sentence, and that is the anchor: a
    case-insensitive search for "MI" lands inside "Medicine" before the mention the annotators
    labelled. Word boundary outranks case. ``None`` when the concept does not occur at all.
    """

    if not concept:
        return None
    upper = re.escape(concept.upper())
    lower = re.escape(concept.lower())
    for anchor, pattern, haystack in (
        ("upper_boundary", _BOUNDED.format(upper), sentence),
        ("boundary", _BOUNDED.format(lower), sentence.lower()),
        ("upper_anywhere", upper, sentence),
        ("anywhere", lower, sentence.lower()),
    ):
        match = re.search(pattern, haystack)
        if match is not None:
            return match.start(), anchor
    return None


def fetch(destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    with urllib.request.urlopen(CORPUS_URL, timeout=60) as response:
        destination.write_bytes(response.read())


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_cases(path: Path) -> tuple[list[CorpusCase], int]:
    """Every row as a case, and how many rows were skipped (too few fields, or a concept that
    does not occur in its sentence).

    The file is tab separated with quoted fields; sentences contain embedded quotes, so it goes
    through a CSV reader rather than a split on tabs.
    """

    cases: list[CorpusCase] = []
    skipped = 0
    with path.open(newline="", encoding="utf-8", errors="replace") as handle:
        for index, row in enumerate(csv.reader(handle, delimiter="\t", quotechar='"')):
            if len(row) < 7:
                skipped += 1
                continue
            sentence = row[SENTENCE].strip()
            concept = row[CONCEPT].strip()
            located = locate_concept(sentence, concept)
            if located is None:
                skipped += 1
                continue
            start, anchor = located
            cases.append(
                CorpusCase(
                    identifier=f"{row[REPORT]}:{index}",
                    sentence=sentence,
                    concept=concept,
                    start=start,
                    end=start + len(concept),
                    gold_negated=row[NEGATION].strip() == "Negated",
                    temporality=row[TEMPORALITY].strip(),
                    experiencer=row[EXPERIENCER].strip(),
                    anchor=anchor,
                )
            )
    return cases, skipped
