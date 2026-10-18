"""Whether a model reproduces a benchmark's public text better when it is told where it came from.

Guided versus general instruction (Golchin and Surdeanu 2024, "Time travel in LLMs"): the model
sees the first half of an instance and is asked to finish it, once told the source and asked for
the text exactly as it appears there, once only asked to finish it. Predictable text is finished
well under both; memorized text is finished better when the source is named. The comparison is
paired on the instance, so how predictable the text is cancels.

Pre-registered for ChartRead in ``docs/chartread-contamination-probe.md``. Nothing here
reads a corpus or calls a model; the script does both.
"""

from __future__ import annotations

import math
import random
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Final

PROMPT_VERSION: Final = "contamination-v1"
MIN_WORDS: Final = 14
MAX_WORDS: Final = 60
#: ROUGE-L at or above which a completion is counted as near-verbatim.
NEAR_VERBATIM: Final = 0.75

GUIDED: Final = (
    "You are given the first piece of an instance from {source}. Finish the second piece of the "
    "instance exactly as it appears in {source}. Rely only on the original form of the instance "
    "in that source. Reply with the second piece only."
)
GENERAL: Final = (
    "Finish the second piece based on the first piece, so that the two pieces become a single "
    "instance. Reply with the second piece only."
)
INSTRUCTIONS: Final = ("guided", "general")

_SENTENCE_END: Final = re.compile(r"(?<=[.!?])\s+|\n+")
_TOKEN: Final = re.compile(r"[a-z0-9]+")


@dataclass(frozen=True, slots=True)
class Instance:
    """One passage of a source, split where the model takes over."""

    instance_id: str
    source: str
    prefix: str
    reference: str


def sentences(text: str) -> list[str]:
    """Split at ``.``, ``!`` or ``?`` followed by whitespace, or at a line break."""

    return [piece.strip() for piece in _SENTENCE_END.split(text) if piece.strip()]


def eligible(passage: str) -> bool:
    return MIN_WORDS <= len(passage.split()) <= MAX_WORDS


def split_instance(instance_id: str, source: str, passage: str) -> Instance:
    """The first ⌊n/2⌋ whitespace words are shown; the rest is the reference."""

    words = passage.split()
    half = len(words) // 2
    return Instance(instance_id, source, " ".join(words[:half]), " ".join(words[half:]))


def sample(passages: Iterable[tuple[str, str]], n: int, seed: int) -> list[tuple[str, str]]:
    """``n`` distinct eligible passages, by id, drawn with ``seed`` from a sorted pool."""

    first: dict[str, str] = {}
    for passage_id, passage in passages:
        if eligible(passage):
            first.setdefault(passage, passage_id)
    pool = sorted((passage_id, passage) for passage, passage_id in first.items())
    if len(pool) < n:
        raise ValueError(f"only {len(pool)} eligible passages, {n} needed")
    return random.Random(seed).sample(pool, n)


def render(instance: Instance, *, guided: bool, source_name: str) -> list[dict[str, str]]:
    system = GUIDED.format(source=source_name) if guided else GENERAL
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": f"First piece: {instance.prefix}"},
    ]


def tokens(text: str) -> list[str]:
    return _TOKEN.findall(text.lower())


def _lcs(a: Sequence[str], b: Sequence[str]) -> int:
    previous = [0] * (len(b) + 1)
    for x in a:
        current = [0]
        for j, y in enumerate(b, start=1):
            current.append(previous[j - 1] + 1 if x == y else max(previous[j], current[j - 1]))
        previous = current
    return previous[-1]


def rouge_l(candidate: Sequence[str], reference: Sequence[str]) -> float:
    """ROUGE-L F1 over token lists; 0 when either is empty."""

    if not candidate or not reference:
        return 0.0
    common = _lcs(candidate, reference)
    if common == 0:
        return 0.0
    precision, recall = common / len(candidate), common / len(reference)
    return 2 * precision * recall / (precision + recall)


def completion(reply: str | None, prefix: str) -> list[str]:
    """The reply's tokens, with a repeated first piece removed from its start."""

    if not reply:
        return []
    got, shown = tokens(reply), tokens(prefix)
    if shown and got[: len(shown)] == shown:
        return got[len(shown) :]
    return got


def sign_test_greater(positive: int, negative: int) -> float:
    """One-sided exact sign test: P(X >= positive) for X ~ Binomial(positive + negative, 1/2)."""

    n = positive + negative
    if n == 0:
        return 1.0
    return min(1.0, sum(math.comb(n, k) for k in range(positive, n + 1)) * 0.5**n)


def score(
    instances: Sequence[Instance], replies: Mapping[tuple[str, str], str | None]
) -> dict[str, Any]:
    """Per source: mean ROUGE-L and near-verbatim count under each instruction, and the guided
    minus general sign test. ``replies`` is keyed by ``(instance_id, instruction)``."""

    report: dict[str, Any] = {}
    for source in sorted({i.source for i in instances}):
        group = [i for i in instances if i.source == source]
        scores = {
            name: [
                rouge_l(
                    completion(replies.get((i.instance_id, name)), i.prefix), tokens(i.reference)
                )
                for i in group
            ]
            for name in INSTRUCTIONS
        }
        differences = [g - h for g, h in zip(scores["guided"], scores["general"], strict=True)]
        positive = sum(d > 0 for d in differences)
        negative = sum(d < 0 for d in differences)
        report[source] = {
            "n": len(group),
            **{
                name: {
                    "mean_rouge_l": round(sum(values) / len(values), 4),
                    "near_verbatim": sum(v >= NEAR_VERBATIM for v in values),
                    "unanswered": sum(not replies.get((i.instance_id, name)) for i in group),
                }
                for name, values in scores.items()
            },
            "guided_better": positive,
            "general_better": negative,
            "p": sign_test_greater(positive, negative),
        }
    return report
