"""A clinician rating sheet as a fillable PDF, and the filled form read back into the sheet.

The CSV a packet writes stays the instrument of record: its rows, refs and order are what the key
maps. The form is built from that blind sheet alone, never from the key, and shows the same rows
in the same order, one card per item, with the instructions on the first page. Reading a filled
form gives back the sheet's own columns, so ``chart_read.read_ratings`` and ``agreement`` read it
unchanged.

A form is matched to its sheet by the sheet's SHA-256, carried in a read-only field, and by its
refs. A form for another sheet is refused rather than read, and an answer the form cannot give
cleanly is left empty and reported, never guessed.
"""

from __future__ import annotations

import csv
import hashlib
import html
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final

import pymupdf

from clinical_eval.chart_read import (
    ABSTAIN_OPTION_B,
    ABSTAIN_OPTION_C,
    OPTION_A,
    PACKET_FIELDS,
)

#: PyMuPDF types little of the form API and sets its widget constants at import, so the module
#: is held as ``Any`` here rather than behind an ignore on every call.
_mu: Any = pymupdf

PAGE: Final = _mu.paper_rect("letter")
MARGIN: Final = 54.0
TOP: Final = 92.0
BOTTOM: Final = PAGE.height - 50.0
WIDTH: Final = PAGE.width - 2 * MARGIN
PAD: Final = 8.0
GAP: Final = 14.0
#: Below the excerpt: a space, the answer row, the flag row, the comment box, the rule.
ROWS: Final = (8.0, 22.0, 18.0, 24.0)
CONTROLS: Final = sum(ROWS) + GAP

INK: Final = (0.13, 0.13, 0.13)
MUTED: Final = (0.42, 0.42, 0.42)
RULE: Final = (0.80, 0.80, 0.80)
PANEL: Final = (0.955, 0.955, 0.955)
FIELD: Final = (0.93, 0.96, 1.0)

SHEET_FIELD: Final = "sheet_sha256"
INITIALS_FIELD: Final = "rater_initials"
DATE_FIELD: Final = "rater_date"

LEGEND: Final = (
    "<b>A</b> = currently present in the patient. &nbsp; <b>B</b> = not: denied or ruled out, "
    "only suspected or possible, resolved in the past, someone else's (e.g. a relative), or "
    "future or conditional."
)

CSS: Final = """
* { font-family: sans-serif; color: #222222; }
body { font-size: 11pt; line-height: 1.45; }
p { margin: 0 0 6pt 0; }
h1 { font-size: 20pt; margin: 0 0 4pt 0; }
h2 { font-size: 12.5pt; margin: 10pt 0 3pt 0; }
ul { margin: 0 0 4pt 0; padding-left: 14pt; }
li { margin: 0 0 3pt 0; }
.sub { color: #555555; font-size: 11pt; }
.small { font-size: 8.5pt; color: #555555; line-height: 1.35; }
.head { font-size: 12pt; }
.ref { color: #555555; }
.excerpt { font-size: 10.5pt; line-height: 1.4; }
.dense { font-size: 8.5pt; line-height: 1.3; }
.q { font-size: 11pt; margin: 2pt 0 0 0; }
ol { margin: 0; padding-left: 16pt; }
.mark { font-weight: bold; background-color: #fff1a8; }
.label { font-size: 10.5pt; }
.cell { font-size: 9.5pt; line-height: 1.3; }
.tiny { font-size: 7.5pt; line-height: 1.3; color: #555555; }
table { border-collapse: collapse; }
td { padding: 2pt 10pt 2pt 0; vertical-align: top; font-size: 10.5pt; }
"""

_SPACES: Final = re.compile(r"[ \t]{2,}")


@dataclass(frozen=True, slots=True)
class Sheet:
    """A packet's blind sheet: its rows in order and the digest of its bytes."""

    path: Path
    rows: tuple[dict[str, str], ...]
    sha256: str

    @property
    def refs(self) -> tuple[str, ...]:
        return tuple(row["ref"] for row in self.rows)


@dataclass(slots=True)
class FilledForm:
    """What a filled form says, in the sheet's columns, and what it could not say cleanly."""

    rows: list[dict[str, str]]
    initials: str
    date: str
    comment: str = ""
    unanswered: list[str] = field(default_factory=list)
    conflicts: list[str] = field(default_factory=list)


def load_sheet(path: Path) -> Sheet:
    data = path.read_bytes()
    with path.open(newline="", encoding="utf-8-sig") as handle:
        rows = tuple(dict(row) for row in csv.DictReader(handle))
    if not rows or tuple(rows[0]) != PACKET_FIELDS:
        raise ValueError(f"{path} is not a rating sheet with columns {PACKET_FIELDS}")
    return Sheet(path, rows, hashlib.sha256(data).hexdigest())


def display(text: str) -> str:
    """The excerpt as shown: runs of spaces closed up, line breaks kept, every word unchanged."""

    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    return "\n".join(_SPACES.sub(" ", line).rstrip() for line in lines).strip("\n")


def marked(text: str, finding: str) -> str:
    """Escaped excerpt with every occurrence of the finding emphasised, any case or spacing."""

    words = finding.split()
    if not words:
        return html.escape(text).replace("\n", "<br>")
    pattern = re.compile(r"\s+".join(re.escape(word) for word in words), re.IGNORECASE)
    pieces, last = [], 0
    for match in pattern.finditer(text):
        pieces.append(html.escape(text[last : match.start()]))
        pieces.append(f'<span class="mark">{html.escape(match.group(0))}</span>')
        last = match.end()
    pieces.append(html.escape(text[last:]))
    return "".join(pieces).replace("\n", "<br>")


def _height(body: str, width: float) -> float:
    """How tall ``body`` sets at ``width``, measured on a scratch page."""

    scratch = _mu.open()
    page = scratch.new_page(width=width + 10, height=3000)
    spare, _ = page.insert_htmlbox(_mu.Rect(0, 0, width, 3000), body, css=CSS, scale_low=1)
    scratch.close()
    if spare < 0:
        raise ValueError("a card does not fit on one page")
    return 3000 - float(spare)


def _text(page: Any, rect: _mu.Rect, body: str) -> None:
    spare, _ = page.insert_htmlbox(rect, body, css=CSS, scale_low=1)
    if spare < 0:
        raise ValueError("text did not fit its measured box")


def _text_field(page: Any, name: str, rect: _mu.Rect, *, multiline: bool = False) -> None:
    widget = _mu.Widget()
    widget.field_type = _mu.PDF_WIDGET_TYPE_TEXT
    widget.field_name = name
    widget.rect = rect
    widget.text_font = "Helv"
    widget.text_fontsize = 10
    widget.fill_color = FIELD
    widget.border_color = MUTED
    widget.border_width = 0.6
    if multiline:
        widget.field_flags = _mu.PDF_TX_FIELD_IS_MULTILINE
    page.add_widget(widget)


def _checkbox(page: Any, name: str, rect: _mu.Rect) -> None:
    widget = _mu.Widget()
    widget.field_type = _mu.PDF_WIDGET_TYPE_CHECKBOX
    widget.field_name = name
    widget.rect = rect
    widget.field_value = False
    widget.fill_color = (1, 1, 1)
    widget.border_color = INK
    widget.border_width = 1
    page.add_widget(widget)


def _radio_group(document: Any, page: Any, name: str, buttons: Sequence[tuple[str, Any]]) -> int:
    """One field whose kids are the buttons, each turning on under its own letter.

    PyMuPDF writes every radio button as a field of its own, each turning on as ``Yes``; two of
    those under one name turn on together. The kids are rewritten under a parent so the group is
    exclusive and its value is the letter. Returns the parent's xref.
    """

    kids: list[tuple[str, int]] = []
    for letter, rect in buttons:
        widget = _mu.Widget()
        widget.field_type = _mu.PDF_WIDGET_TYPE_RADIOBUTTON
        widget.field_name = f"{name}__{letter}"
        widget.rect = rect
        widget.field_value = False
        widget.fill_color = (1, 1, 1)
        widget.border_color = INK
        widget.border_width = 1
        kids.append((letter, page.add_widget(widget).xref))
    parent = document.get_new_xref()
    refs = " ".join(f"{xref} 0 R" for _, xref in kids)
    document.update_object(parent, f"<< /FT /Btn /Ff 49152 /T ({name}) /V /Off /Kids [{refs}] >>")
    for letter, xref in kids:
        source = document.xref_object(xref)
        source = re.sub(r"^  /(T|FT|Ff|V|NM|DA) .*\n", "", source, flags=re.MULTILINE)
        source = source.replace("/Yes ", f"/{letter} ")
        source = source.rstrip().removesuffix(">>").rstrip() + f"\n  /Parent {parent} 0 R\n>>"
        document.update_object(xref, source)
    return int(parent)


def _alone(raters: int) -> str:
    if raters > 1:
        return (
            "Work alone. Do not discuss the items with anyone, and do not look anything up about "
            "the study, until you have sent the form back."
        )
    return "Work alone, and do not look anything up in the project until the form is returned."


def _returning(name: str, raters: int, every: str) -> str:
    return f"""
<h2>Filling in and sending back</h2>
<ul>
<li>{_alone(raters)}</li>
<li>Open this file in <b>Adobe Acrobat Reader</b> (free) or <b>macOS Preview</b>, click your
answers, and save. In a web browser, save with its download button, or your answers are lost.</li>
<li>When {every}, save the file with your initials added to its name, for example
<b>{html.escape(name)}_AOA.pdf</b>, and send it back.</li>
<li>Questions about the form: ask the person who sent it to you.</li>
</ul>
"""


#: Shown to a clinician taking part (not to the investigator rating their own sheets).
TAKING_PART: Final = """
<h2>About taking part</h2>
<p>This form is part of a research study of how software reads clinical notes. Taking part is
voluntary, and you may stop at any time without giving a reason. The excerpts identify no real
patient. Your answers are kept under your initials and reported by rater number, never by
name.</p>
"""


def _labels_body(sheet: Sheet, raters: int) -> str:
    return """
<h2>The question</h2>
<p>Each item shows a note excerpt and one finding, highlighted. Decide whether the text states
that the patient has this finding <b>now</b>.</p>
<ul>
<li><b>A &mdash; Currently present.</b> The text states this finding is currently present in the
patient.</li>
<li><b>B &mdash; Not currently present.</b> The finding is denied or ruled out; only suspected or
possible; resolved in the past; about someone other than the patient (for example a relative); or
mentioned only as a future or conditional possibility.</li>
</ul>
<h2>Optional flags</h2>
<ul>
<li><b>U</b> &mdash; the text reads unnatural: no clinician would write it.</li>
<li><b>Q</b> &mdash; ambiguous: more than one answer is defensible.</li>
</ul>
<p>Still choose A or B when you flag, and say why in the comment box if you can.</p>
<h2>How to judge</h2>
<ul>
<li>Use only what the excerpt says. Do not assume facts it does not state.</li>
<li>A section label (for example &ldquo;FAMILY HISTORY:&rdquo; or &ldquo;NEGATIVE FOR:&rdquo;)
applies to the list under it.</li>
<li>In a real report, if you are unsure whether an event earlier in the same admission counts as
present now, give your best reading and tick Q.</li>
</ul>
<h2>Examples (made up; not in the form)</h2>
<table>
<tr><td>&ldquo;Denies fever. Reports cough.&rdquo;</td><td>finding: cough</td>
<td><b>A</b></td></tr>
<tr><td>&ldquo;Mother has type 2 diabetes.&rdquo;</td><td>finding: diabetes</td>
<td><b>B</b></td></tr>
<tr><td>&ldquo;Possible pneumonia; CXR pending.&rdquo;</td><td>finding: pneumonia</td>
<td><b>B</b></td></tr>
</table>
"""


def _medication_body(sheet: Sheet, raters: int) -> str:
    return """
<h2>The question</h2>
<p>Each item shows a note excerpt and one drug, highlighted. Decide whether the text states that
the patient is taking this drug <b>now</b>.</p>
<ul>
<li><b>A &mdash; Currently taking.</b> The text states the patient is currently taking it.</li>
<li><b>B &mdash; Not currently taking.</b> The text does not state that: the drug was stopped, is
only planned or conditional, was declined or never started, or is to be avoided.</li>
</ul>
<h2>Optional flags</h2>
<ul>
<li><b>U</b> &mdash; the text reads unnatural: no clinician would write it.</li>
<li><b>Q</b> &mdash; ambiguous: more than one answer is defensible.</li>
</ul>
<p>Still choose A or B when you flag, and say why in the comment box if you can.</p>
<h2>How to judge</h2>
<ul>
<li>Use only what the excerpt says. Do not assume facts it does not state.</li>
<li>Judge the patient's state as the note is written, not what should happen next.</li>
</ul>
<h2>Examples (made up; not in the form)</h2>
<table>
<tr><td>&ldquo;Continues warfarin; INR at goal.&rdquo;</td><td>drug: warfarin</td>
<td><b>A</b></td></tr>
<tr><td>&ldquo;Prednisone taper completed last month.&rdquo;</td><td>drug: prednisone</td>
<td><b>B</b></td></tr>
<tr><td>&ldquo;Will start insulin if sugars stay high.&rdquo;</td><td>drug: insulin</td>
<td><b>B</b></td></tr>
</table>
"""


def _match_body(sheet: Sheet, raters: int) -> str:
    """The omission-verifier match question (A.5), as registered, with nothing added to it."""

    return f"""
<h2>The question</h2>
<p>Each item shows one <b>target</b>, highlighted: a clinical action from a management plan, or a
statement from an answer to a patient's question. It also shows the <b>output of a reviewer</b>
that checked that plan or answer. Read the question printed at the end of the item and answer
it:</p>
<ul>
<li><b>A &mdash; Yes.</b> The reviewer says the plan (or answer) does not include the target, or
recommends adding it.</li>
<li><b>B &mdash; No.</b> The reviewer does not.</li>
</ul>
<h2>How to answer</h2>
<ul>
<li>Use only the reviewer output shown. You do not see the plan or answer itself.</li>
<li>You are not asked whether the target or the reviewer is clinically right.</li>
<li>Answer each item on its own. There is no time limit.</li>
</ul>
<h2>Optional flags</h2>
<ul>
<li><b>U</b> &mdash; the item is unreadable or garbled.</li>
<li><b>Q</b> &mdash; ambiguous: more than one answer is defensible.</li>
</ul>
<p>Still choose A or B when you flag, and say why in the comment box if you can. The form has
{len(sheet.rows)} items.</p>
"""


def _review_body(sheet: Sheet, raters: int) -> str:
    """The omission-verifier clinician baseline: V1's own instruction, printed on each item.

    Says nothing of how the texts were made or what the study compares, so each is read as it
    stands.
    """

    return f"""
<h2>The task</h2>
<p>Each item shows either a consultation case with a specialist's management plan, or a patient's
question with an answer to it. An instruction is printed above each item: follow it, as you would
if the plan or answer were about to be sent.</p>
<ul>
<li>Write each issue you find in your own words, <b>one per line</b>, in the box under the
item.</li>
<li>If you find no issue, write <b>None</b>. Please do not leave a box empty.</li>
</ul>
<h2>How to answer</h2>
<ul>
<li>Use the item as shown and your own clinical knowledge. Do not look anything up.</li>
<li>Answer the items in order, each on its own, and do not go back to change an answer.</li>
<li>Write as much or as little as you would in practice. There is no time limit.</li>
</ul>
<p>Please finish this form before you open any other form for this study, and before you read
anything about the study. It has {len(sheet.rows)} items.</p>
"""


def _sentence(option: str) -> str:
    """A model option's words after its "Yes —"/"No —" lead, as a sentence: same words, capital."""

    words = option.split(" — ", 1)[1]
    return html.escape(words[0].upper() + words[1:])


def _reading_body(sheet: Sheet, raters: int) -> str:
    """The clinician baseline (addendum 4): the models' own three options, word for word.

    Says nothing of how the excerpts were made, so a rater reads each one as it stands.
    """

    return f"""
<h2>The question</h2>
<p>Each item shows an excerpt from a clinical note, as a computer system might pass it on to a
reader, and one finding, highlighted. Using only the text shown, answer: <b>according to this
text, is the finding currently present in the patient?</b></p>
<ul>
<li><b>A &mdash; Yes.</b> {_sentence(OPTION_A)}</li>
<li><b>B &mdash; No.</b> {_sentence(ABSTAIN_OPTION_B)}</li>
<li><b>C &mdash; Cannot tell.</b> {_sentence(ABSTAIN_OPTION_C)}</li>
</ul>
<h2>How to answer</h2>
<ul>
<li>Use only what the excerpt says. Do not assume facts it does not state.</li>
<li>Answer the items in order, each on its own, and do not go back to change an answer.</li>
<li>Give your first considered reading. There is no time limit.</li>
<li>Extra spaces have been removed; the words are exactly as written.</li>
</ul>
<h2>Examples (made up; not in the form)</h2>
<table>
<tr><td>&ldquo;Denies fever. Reports cough.&rdquo;</td><td>finding: cough</td>
<td><b>A</b></td></tr>
<tr><td>&ldquo;Mother has type 2 diabetes.&rdquo;</td><td>finding: type 2 diabetes</td>
<td><b>B</b></td></tr>
<tr><td>&ldquo;Seen today. Vitals stable.&rdquo;</td><td>finding: anemia</td>
<td><b>C</b></td></tr>
</table>
<p>Please finish this form before you open any other ChartRead form, and before you read
anything about the study. It has {len(sheet.rows)} items.</p>
"""


def _harm_body(sheet: Sheet, raters: int) -> str:
    """The harm rating (addendum 4): a severity per wrong reading, AHRQ Common Formats levels."""

    return """
<h2>The situation</h2>
<p>An AI system reads a patient's chart and writes a summary of the patient's current problems.
A clinician preparing for an adult outpatient follow-up visit relies on that summary and does not
re-read the note. Each row is one way the summary can get one finding wrong:</p>
<ul>
<li><b>Listed as current</b>: the summary lists the finding as a current problem, but the note
said something else, given in the row (for example, that it was denied, or that a relative has
it).</li>
<li><b>Listed as absent</b>: the summary says the patient does not have the finding, but the note
said the patient has it now.</li>
</ul>
<h2>The question</h2>
<p>For each row: <b>what is the most likely harm to this patient</b> if the clinician acts on the
wrong summary? Rate the likely outcome, not the worst case.</p>
<table>
<tr><td><b>None</b></td><td>No harm; the error would most likely be caught or change
nothing.</td></tr>
<tr><td><b>Mild</b></td><td>Minimal symptoms or loss of function, or little or no intervention
(for example an extra test or visit).</td></tr>
<tr><td><b>Moderate</b></td><td>Harm that affects function or quality of life, or that needs an
intervention (for example an unneeded treatment, or a delayed one).</td></tr>
<tr><td><b>Severe</b></td><td>Major loss of function, a life-threatening event, or a major
intervention.</td></tr>
<tr><td><b>Death</b></td><td>Death is the most likely outcome.</td></tr>
</table>
<p class="small">Levels after the AHRQ Common Formats Harm Scale (v1.2). The rows for one finding
are grouped together. Tick <b>Unsure</b> where two levels both fit, and still choose one.</p>
"""


@dataclass(frozen=True, slots=True)
class Option:
    """One answer: the value written to the sheet, and what the form prints beside its button."""

    letter: str
    shown: str


@dataclass(frozen=True, slots=True)
class Instrument:
    """What a form asks and how it lays out its rows; the sheet's columns are the same for all.

    ``summary`` is the cover's "in short" box. ``question`` is asked on every card, of the finding.
    ``table`` sets one row per item, grouped by finding (the harm rating), instead of a card with
    an excerpt. ``flags`` adds the U and Q tick boxes of the label validation.
    """

    heading: str
    options: tuple[Option, ...]
    legend: str
    body: Callable[[Sheet, int], str]
    summary: tuple[str, ...]
    every: str
    question: str = ""
    flags: bool = True
    table: bool = False
    unsure: str = "Q"
    #: The excerpt's CSS class; ``dense`` sets long reviewer outputs smaller so each still fits
    #: one card, every word shown.
    excerpt: str = "excerpt"
    #: The study named in every page's banner and the file's title.
    study: str = "ChartRead"
    #: Height of a free-text answer box. Nonzero replaces the lettered answers with the box, and
    #: the sheet's answer is then what the rater wrote.
    review: float = 0.0

    @property
    def letters(self) -> tuple[str, ...]:
        return tuple(option.letter for option in self.options)


LABELS: Final = Instrument(
    heading="ChartRead: is the finding present now?",
    options=(
        Option("A", "<b>A</b> &nbsp;Currently present"),
        Option("B", "<b>B</b> &nbsp;Not currently present"),
    ),
    legend=LEGEND,
    body=_labels_body,
    summary=(
        "Read each excerpt and the highlighted finding.",
        "Answer <b>A</b> (present now) or <b>B</b> (not present now).",
        "Optionally tick <b>U</b> or <b>Q</b>, and add a comment.",
        "Save with your initials in the file name, and send it back.",
    ),
    every="every item has A or B",
    question="Does the text state that <b>{finding}</b> is currently present in the patient?",
)
MEDICATION: Final = Instrument(
    heading="ChartRead: is the drug being taken now?",
    options=(
        Option("A", "<b>A</b> &nbsp;Currently taking"),
        Option("B", "<b>B</b> &nbsp;Not currently taking"),
    ),
    legend=(
        "<b>A</b> = the text states the patient is currently taking it. &nbsp; <b>B</b> = it does "
        "not: stopped, only planned or conditional, declined or never started, or to be avoided."
    ),
    body=_medication_body,
    summary=(
        "Read each excerpt and the highlighted drug.",
        "Answer <b>A</b> (taking it now) or <b>B</b> (not taking it now).",
        "Optionally tick <b>U</b> or <b>Q</b>, and add a comment.",
        "Save with your initials in the file name, and send it back.",
    ),
    every="every item has A or B",
    question="Does the text state that the patient is currently taking <b>{finding}</b>?",
)
READING: Final = Instrument(
    heading="ChartRead: reading note excerpts",
    options=(
        Option("A", "<b>A</b> &nbsp;Yes, present now"),
        Option("B", "<b>B</b> &nbsp;No, stated not present"),
        Option("C", "<b>C</b> &nbsp;Cannot tell"),
    ),
    legend=(
        "<b>A</b> = the text states it is currently present in the patient. &nbsp; <b>B</b> = the "
        "text states it is not (denied, ruled out, suspected only, resolved, someone else's, or "
        "future only). &nbsp; <b>C</b> = the text does not say."
    ),
    body=_reading_body,
    summary=(
        "Read each excerpt and the highlighted finding.",
        "Answer <b>A</b> (yes), <b>B</b> (no) or <b>C</b> (cannot tell), using only the text.",
        "Go in order, and do not go back.",
        "Save with your initials in the file name, and send it back.",
    ),
    every="every item has A, B or C",
    question="According to this text, is <b>{finding}</b> currently present in the patient?",
    flags=False,
)
HARM_LEVELS: Final = ("H0", "H1", "H2", "H3", "H4")
HARM: Final = Instrument(
    heading="ChartRead: harm of a wrong reading",
    options=tuple(
        Option(letter, label)
        for letter, label in zip(
            HARM_LEVELS, ("None", "Mild", "Moderate", "Severe", "Death"), strict=True
        )
    ),
    legend=(
        "Most likely harm if a clinician acts on the wrong summary. <b>None</b> &middot; "
        "<b>Mild</b>: minimal, extra test or visit &middot; <b>Moderate</b>: affects function, or "
        "needs (or delays) an intervention &middot; <b>Severe</b>: major harm or life-threatening "
        "&middot; <b>Death</b>."
    ),
    body=_harm_body,
    summary=(
        "Each row is one way an AI summary gets one finding wrong.",
        "Choose the most likely harm: <b>None</b>, <b>Mild</b>, <b>Moderate</b>, <b>Severe</b> "
        "or <b>Death</b>.",
        "Tick <b>Unsure</b> where two levels both fit.",
        "Save with your initials in the file name, and send it back.",
    ),
    every="every row has a level",
    flags=False,
    table=True,
)
MATCH: Final = Instrument(
    heading="Omission verifier: does the reviewer name the target?",
    options=(
        Option("A", "<b>A</b> &nbsp;Yes"),
        Option("B", "<b>B</b> &nbsp;No"),
    ),
    legend=(
        "<b>A</b> = the reviewer says the plan (or answer) does not include the target, or "
        "recommends adding it. &nbsp; <b>B</b> = the reviewer does not."
    ),
    body=_match_body,
    summary=(
        "Read the highlighted target and the reviewer's output.",
        "Answer the question at the end of each item: <b>A</b> (yes) or <b>B</b> (no).",
        "Optionally tick <b>U</b> or <b>Q</b>, and add a comment.",
        "Save with your initials in the file name, and send it back.",
    ),
    every="every item has A or B",
    excerpt="dense",
    study="Omission verifier",
)
REVIEW: Final = Instrument(
    heading="Omission verifier: clinician review",
    options=(),
    legend=(
        "Write each issue you find in your own words, one per line. Write <b>None</b> if you find "
        "none."
    ),
    body=_review_body,
    summary=(
        "Read each case and plan, or question and answer.",
        "Follow the instruction above it: write each issue you find, one per line, or <b>None</b>.",
        "Go in order, and do not go back.",
        "Save with your initials in the file name, and send it back.",
    ),
    every="every item has your review written (or None)",
    question="{finding}",
    flags=False,
    excerpt="dense",
    study="Omission verifier",
    review=120.0,
)
INSTRUMENTS: Final = {
    "labels": LABELS,
    "medication": MEDICATION,
    "reading": READING,
    "harm": HARM,
    "match": MATCH,
    "review": REVIEW,
}

#: The "in short" box on the cover.
SHORT_FILL: Final = (1.0, 0.975, 0.86)


def _page_furniture(page: Any, banner: str, legend: str) -> None:
    _text(
        page,
        _mu.Rect(MARGIN, 30, MARGIN + WIDTH, TOP - 8),
        f'<p class="small"><b>{html.escape(banner)}</b></p><p class="small">{legend}</p>',
    )
    page.draw_line((MARGIN, TOP - 6), (MARGIN + WIDTH, TOP - 6), color=RULE, width=0.6)


def _cover(
    document: Any,
    sheet: Sheet,
    instrument: Instrument,
    *,
    title: str,
    part: str,
    raters: int,
    name: str,
    participant: bool,
) -> None:
    cover = document.new_page(width=PAGE.width, height=PAGE.height)
    who = "one clinician" if raters == 1 else f"{raters} clinicians, each on their own copy"
    if raters == 2:
        who = "two clinicians, each on their own copy"
    lead = f"{html.escape(part)} &middot; " if part else ""
    heading = (
        f"<h1>{html.escape(instrument.heading)}</h1>"
        f'<p class="sub">{lead}{html.escape(title)} &middot; {len(sheet.rows)} items &middot; '
        f"filled by {who}</p>"
    )
    heading_h = _height(heading, WIDTH)
    _text(cover, _mu.Rect(MARGIN, 40, MARGIN + WIDTH, 40 + heading_h + 1), heading)
    y = 40 + heading_h + 6
    _text(cover, _mu.Rect(MARGIN, y + 3, MARGIN + 90, y + 22), "<p>Your initials</p>")
    _text_field(cover, INITIALS_FIELD, _mu.Rect(MARGIN + 92, y, MARGIN + 200, y + 22))
    _text(cover, _mu.Rect(MARGIN + 240, y + 3, MARGIN + 280, y + 22), "<p>Date</p>")
    _text_field(cover, DATE_FIELD, _mu.Rect(MARGIN + 282, y, MARGIN + 420, y + 22))
    y += 32
    short = "<p><b>In short</b></p><ol>" + "".join(f"<li>{s}</li>" for s in instrument.summary)
    short += "</ol>"
    short_h = _height(short, WIDTH - 2 * PAD)
    box = _mu.Rect(MARGIN, y, MARGIN + WIDTH, y + short_h + 2 * PAD)
    cover.draw_rect(box, color=None, fill=SHORT_FILL, radius=0.04)
    _text(cover, _mu.Rect(MARGIN + PAD, y + PAD, MARGIN + WIDTH - PAD, box.y1 - PAD + 1), short)
    y = box.y1 + 4
    identity = _mu.Widget()
    identity.field_type = _mu.PDF_WIDGET_TYPE_TEXT
    identity.field_name = SHEET_FIELD
    identity.field_value = sheet.sha256
    identity.field_flags = _mu.PDF_FIELD_IS_READ_ONLY
    identity.rect = _mu.Rect(MARGIN, PAGE.height - 28, MARGIN + 300, PAGE.height - 18)
    identity.text_fontsize = 6
    identity.text_color = MUTED
    identity.border_width = 0
    cover.add_widget(identity)
    body = instrument.body(sheet, raters)
    if participant:
        body += TAKING_PART
    body += _returning(name, raters, instrument.every)
    # The instructions run on to a second page at a section break when they do not fit one.
    page = cover
    for section in re.split(r"(?=<h2>)", body.strip()):
        tall = _height(section, WIDTH)
        if y + tall > PAGE.height - 40:
            page = document.new_page(width=PAGE.width, height=PAGE.height)
            more = '<p class="small"><b>Instructions, continued</b></p>'
            _text(page, _mu.Rect(MARGIN, 40, MARGIN + WIDTH, 58), more)
            y = 60
        _text(page, _mu.Rect(MARGIN, y, MARGIN + WIDTH, y + tall + 1), section)
        y += tall


#: The least a free-text answer box shrinks to, so a long item still fits one page.
REVIEW_FLOOR: Final = 80.0
#: Below a card's excerpt: a space, the answer row, the flag row, the comment row.
CARD_ROWS: Final = (6.0, 22.0, 16.0, 18.0)
CARD_PAD: Final = 6.0


def _cards(
    document: Any, sheet: Sheet, instrument: Instrument, banner: str
) -> tuple[list[tuple[int, int]], float]:
    """One card per item: its number, the question, the excerpt, the answers, then the optional
    flags and comment."""

    page = None
    y = BOTTOM
    starts: list[tuple[int, int]] = []
    total = len(sheet.rows)
    rows = CARD_ROWS if instrument.flags else (*CARD_ROWS[:2], 0.0, CARD_ROWS[3])
    if instrument.review:
        rows = (CARD_ROWS[0], instrument.review + 6, 0.0, CARD_ROWS[3])
    controls = sum(rows) + GAP - 2
    step = WIDTH / max(1, len(instrument.options))
    for number, row in enumerate(sheet.rows, start=1):
        ref, finding = row["ref"], display(row["finding"])
        head = (
            f'<p class="head"><b>Item {number}</b> '
            f'<span class="ref">of {total} &middot; {html.escape(ref)}</span></p>'
        )
        ask = instrument.question.format(finding=html.escape(finding))
        if ask:
            head += f'<p class="q">{ask}</p>'
        excerpt = f'<p class="{instrument.excerpt}">{marked(display(row["text"]), finding)}</p>'
        head_h = _height(head, WIDTH)
        body_h = _height(excerpt, WIDTH - 2 * CARD_PAD)
        card = head_h + 2 + body_h + 2 * CARD_PAD + controls
        # A free-text box gives up height, down to REVIEW_FLOOR, to keep a long item on one page.
        shrink = min(max(0.0, card - (BOTTOM - TOP)), instrument.review - REVIEW_FLOOR)
        card -= max(0.0, shrink)
        if card > BOTTOM - TOP:
            raise ValueError(f"{ref} is too long for one page")
        if page is None or y + card > BOTTOM:
            page = document.new_page(width=PAGE.width, height=PAGE.height)
            _page_furniture(page, banner, instrument.legend)
            y = TOP
        starts.append((document.page_count - 1, number))
        x0, x1 = MARGIN, MARGIN + WIDTH
        _text(page, _mu.Rect(x0, y, x1, y + head_h + 1), head)
        y += head_h + 2
        panel = _mu.Rect(x0, y, x1, y + body_h + 2 * CARD_PAD)
        page.draw_rect(panel, color=None, fill=PANEL, radius=0.04)
        _text(
            page,
            _mu.Rect(x0 + CARD_PAD, y + CARD_PAD, x1 - CARD_PAD, y + CARD_PAD + body_h + 1),
            excerpt,
        )
        y = panel.y1 + rows[0]

        box = 15.0
        if instrument.review:
            answer = _mu.Rect(x0, y, x1, y + instrument.review - max(0.0, shrink))
            _text_field(page, f"answer_{ref}", answer, multiline=True)
        else:
            _radio_group(
                document,
                page,
                f"answer_{ref}",
                [
                    (option.letter, _mu.Rect(x0 + i * step, y, x0 + i * step + box, y + box))
                    for i, option in enumerate(instrument.options)
                ],
            )
        for i, option in enumerate(instrument.options):
            left = x0 + i * step + box + 6
            _text(
                page,
                _mu.Rect(left, y, min(x0 + (i + 1) * step - 5, x1), y + 18),
                f'<p class="label">{option.shown}</p>',
            )
        y += rows[1] - max(0.0, shrink)
        small = 11.0
        if instrument.flags:
            _text(page, _mu.Rect(x0, y, x0 + 60, y + 16), '<p class="small">Optional:</p>')
            _checkbox(page, f"flagU_{ref}", _mu.Rect(x0 + 62, y + 1, x0 + 62 + small, y + 12))
            _text(
                page,
                _mu.Rect(x0 + 62 + small + 5, y, x0 + 250, y + 16),
                '<p class="small"><b>U</b> reads unnatural</p>',
            )
            _checkbox(page, f"flagQ_{ref}", _mu.Rect(x0 + 250, y + 1, x0 + 250 + small, y + 12))
            _text(
                page,
                _mu.Rect(x0 + 250 + small + 5, y, x1, y + 16),
                '<p class="small"><b>Q</b> ambiguous: more than one answer defensible</p>',
            )
        y += rows[2]
        _text(page, _mu.Rect(x0, y + 3, x0 + 90, y + 16), '<p class="small">Comment (optional)</p>')
        _text_field(page, f"comment_{ref}", _mu.Rect(x0 + 92, y, x1, y + rows[3] - 2))
        y += rows[3] + GAP / 2 - 1
        page.draw_line((x0, y), (x1, y), color=RULE, width=0.6)
        y += GAP / 2 - 1
    return starts, y


#: The harm table's columns: ref, the error, one per level, and Unsure.
TABLE_REF: Final = 32.0
TABLE_LEVEL: Final = 42.0
TABLE_UNSURE: Final = 36.0
GENERAL_COMMENT: Final = "comment_general"


def _table(
    document: Any, sheet: Sheet, instrument: Instrument, banner: str
) -> tuple[list[tuple[int, int]], float]:
    """One row per item, under its finding: the rows of one finding share a heading, so a rater
    sets their levels side by side. The level header repeats on every page."""

    levels = len(instrument.options)
    said = WIDTH - TABLE_REF - levels * TABLE_LEVEL - TABLE_UNSURE
    x_said = MARGIN + TABLE_REF
    x_levels = x_said + said
    x_unsure = x_levels + levels * TABLE_LEVEL
    box = 13.0
    small = 11.0

    def header(target: Any, at: float) -> float:
        cells = [
            (MARGIN, TABLE_REF, "Ref"),
            (x_said, said, "Finding, and how the summary got it wrong"),
            *(
                (x_levels + i * TABLE_LEVEL, TABLE_LEVEL, option.shown)
                for i, option in enumerate(instrument.options)
            ),
            (x_unsure, TABLE_UNSURE, "Unsure"),
        ]
        bodies = [
            (left, width, f'<p class="tiny"><b>{label}</b></p>') for left, width, label in cells
        ]
        tall = max(_height(body, width - 2) for _, width, body in bodies)
        for left, width, body in bodies:
            _text(target, _mu.Rect(left, at, left + width - 2, at + tall + 1), body)
        target.draw_line((MARGIN, at + tall), (MARGIN + WIDTH, at + tall), color=INK, width=0.6)
        return at + tall + 4

    page = None
    y = BOTTOM
    starts: list[tuple[int, int]] = []
    previous = ""
    for number, row in enumerate(sheet.rows, start=1):
        ref, finding = row["ref"], display(row["finding"])
        note = f'<p class="cell">{html.escape(display(row["text"]))}</p>'
        height = max(_height(note, said - 8), 16.0) + 8
        title = f'<p class="label"><b>{html.escape(finding)}</b></p>'
        title_h = _height(title, said) + 6
        opens = finding != previous
        need = height + (title_h if opens else 0)
        if page is None or y + need > BOTTOM:
            page = document.new_page(width=PAGE.width, height=PAGE.height)
            _page_furniture(page, banner, instrument.legend)
            y = header(page, TOP)
            if not opens:
                more = f'<p class="label"><b>{html.escape(finding)}</b> (continued)</p>'
                _text(page, _mu.Rect(x_said, y + 3, x_levels, y + title_h), more)
                y += title_h
        if opens:
            page.draw_rect(
                _mu.Rect(MARGIN, y, MARGIN + WIDTH, y + title_h - 1), color=None, fill=PANEL
            )
            _text(page, _mu.Rect(x_said, y + 3, x_levels, y + title_h), title)
            y += title_h
        starts.append((document.page_count - 1, number))
        _text(page, _mu.Rect(MARGIN, y + 4, x_said - 2, y + 18), f'<p class="tiny">{ref}</p>')
        _text(page, _mu.Rect(x_said + 8, y + 4, x_levels - 4, y + height), note)
        middle = y + height / 2 - box / 2
        _radio_group(
            document,
            page,
            f"answer_{ref}",
            [
                (
                    option.letter,
                    _mu.Rect(
                        x_levels + i * TABLE_LEVEL + (TABLE_LEVEL - box) / 2,
                        middle,
                        x_levels + i * TABLE_LEVEL + (TABLE_LEVEL + box) / 2,
                        middle + box,
                    ),
                )
                for i, option in enumerate(instrument.options)
            ],
        )
        left = x_unsure + (TABLE_UNSURE - small) / 2
        tick = _mu.Rect(left, middle + 1, left + small, middle + 1 + small)
        _checkbox(page, f"flag{instrument.unsure}_{ref}", tick)
        y += height
        page.draw_line((x_said, y), (MARGIN + WIDTH, y), color=RULE, width=0.4)
        previous = finding
    assert page is not None
    if y + 70 > BOTTOM:
        page = document.new_page(width=PAGE.width, height=PAGE.height)
        _page_furniture(page, banner, instrument.legend)
        y = TOP
    prompt = '<p class="small">Any comment on the form or on particular rows (optional)</p>'
    _text(page, _mu.Rect(MARGIN, y + 8, MARGIN + WIDTH, y + 22), prompt)
    box_rect = _mu.Rect(MARGIN, y + 24, MARGIN + WIDTH, y + 64)
    _text_field(page, GENERAL_COMMENT, box_rect, multiline=True)
    return starts, y + 64


def build(
    sheet_path: Path,
    out: Path,
    *,
    title: str,
    raters: int = 1,
    instrument: Instrument = LABELS,
    part: str = "",
    participant: bool = False,
) -> dict[str, Any]:
    """Write the fillable form for ``sheet_path``; returns its page and item counts.

    ``part`` names the form's place in a rater's set ("Part 1 of 3"); ``participant`` adds the
    cover's note on taking part, for a clinician who is not the investigator.
    """

    sheet = load_sheet(sheet_path)
    document = _mu.open()
    _cover(
        document,
        sheet,
        instrument,
        title=title,
        part=part,
        raters=raters,
        name=out.stem,
        participant=participant,
    )
    banner = " · ".join(x for x in (instrument.study, part, title) if x)
    layout = _table if instrument.table else _cards
    starts, y = layout(document, sheet, instrument, banner)

    last = document[-1]
    done = (
        f"<p><b>End of the form.</b> Before you send it back: check that {instrument.every}, "
        "save the file, and add your initials to its name.</p>"
    )
    done_h = _height(done, WIDTH - 2 * PAD)
    if y + done_h + 2 * PAD + 8 > BOTTOM:
        last = document.new_page(width=PAGE.width, height=PAGE.height)
        _page_furniture(last, banner, instrument.legend)
        y = TOP
    end = _mu.Rect(MARGIN, y + 8, MARGIN + WIDTH, y + 8 + done_h + 2 * PAD)
    last.draw_rect(end, color=None, fill=SHORT_FILL, radius=0.04)
    _text(last, _mu.Rect(end.x0 + PAD, end.y0 + PAD, end.x1 - PAD, end.y1 - PAD + 1), done)

    pages = document.page_count
    total = len(sheet.rows)
    first_last: dict[int, list[int]] = {}
    for number, item in starts:
        first_last.setdefault(number, []).append(item)
    lead = f"{html.escape(part)} &middot; " if part else ""
    for number in range(pages):
        items = first_last.get(number)
        span = f" &middot; items {items[0]}&ndash;{items[-1]} of {total}" if items else ""
        _text(
            document[number],
            _mu.Rect(MARGIN, PAGE.height - 30, MARGIN + WIDTH, PAGE.height - 14),
            f'<p class="small" style="text-align:right">{lead}Page {number + 1} of {pages}'
            f"{span}</p>",
        )

    catalog = document.pdf_catalog()
    document.xref_set_key(
        catalog,
        "AcroForm/DR",
        "<< /Font << /Helv << /Type /Font /Subtype /Type1 /BaseFont /Helvetica "
        "/Encoding /WinAnsiEncoding >> >> >>",
    )
    document.xref_set_key(catalog, "AcroForm/DA", "(/Helv 0 Tf 0 g)")
    fields = _top_level_fields(document)
    document.xref_set_key(catalog, "AcroForm/Fields", "[" + " ".join(fields) + "]")
    document.set_metadata(
        {
            "title": f"{instrument.study}: {title}",
            "subject": f"Fillable form for {sheet.path.name}, sha256 {sheet.sha256}",
        }
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    # Every text box embeds its own copy of the font; subsetting and content-level deduplication
    # take an 80 MB form to well under one, which is the difference between attachable and not.
    document.subset_fonts()
    document.save(out, garbage=4, deflate=True, deflate_fonts=True)
    document.close()
    return {"form": str(out), "items": len(sheet.rows), "pages": pages, "sheet": sheet.sha256}


def _top_level_fields(document: Any) -> list[str]:
    """Every field with no parent, each once, in page order: radio parents replace their kids."""

    seen: list[str] = []
    for page in document:
        for widget in page.widgets():
            parent = document.xref_get_key(widget.xref, "Parent")
            ref = parent[1] if parent[0] == "xref" else f"{widget.xref} 0 R"
            if ref not in seen:
                seen.append(ref)
    return seen


def _radio_value(document: Any, kids: Sequence[int], letters: Sequence[str]) -> list[str]:
    """The letters this group shows as chosen, from its value and from each kid's appearance.

    A viewer may rename each kid's state to its position in the group (``/0``, ``/1``) and keep
    the letters in the group's ``/Opt``, as the PDF standard allows and macOS Preview does on
    save; a position is read through that table. Preview drops the table when it saves the form
    a second time, leaving only positions; the form placed one kid per letter in the
    instrument's order, so a position is then read as that letter, and only when the group still
    has exactly one kid per letter.
    """

    states: list[tuple[str, str]] = [document.xref_get_key(xref, "AS") for xref in kids]
    options: list[str] = []
    parent = document.xref_get_key(kids[0], "Parent")
    if parent[0] == "xref":
        group = int(parent[1].split()[0])
        states.append(document.xref_get_key(group, "V"))
        table = document.xref_get_key(group, "Opt")
        if table[0] == "array":
            options = re.findall(r"\(([^()\\]*)\)", table[1])
        elif len(kids) == len(letters):
            options = list(letters)
    chosen: set[str] = set()
    for kind, name in states:
        state = name.lstrip("/")
        if kind == "name" and state.isdigit() and int(state) < len(options):
            state = options[int(state)]
        if kind == "name" and state in letters:
            chosen.add(state)
    return sorted(chosen)


def read(form_path: Path, sheet_path: Path, instrument: Instrument = LABELS) -> FilledForm:
    """The filled form as sheet rows. A form for another sheet raises; an answer the form gives
    for both letters, or for neither, is left empty and listed. A free-text form's answer is
    what the rater wrote, listed unanswered when blank."""

    sheet = load_sheet(sheet_path)
    document = _mu.open(form_path)
    texts: dict[str, str] = {}
    checks: dict[str, bool] = {}
    radios: dict[str, list[int]] = {}
    for page in document:
        for widget in page.widgets():
            name = widget.field_name or ""
            if widget.field_type == _mu.PDF_WIDGET_TYPE_RADIOBUTTON:
                radios.setdefault(name, []).append(widget.xref)
            elif widget.field_type == _mu.PDF_WIDGET_TYPE_CHECKBOX:
                checks[name] = widget.field_value not in (False, None, "", "Off")
            else:
                texts[name] = str(widget.field_value or "")
    recorded = texts.get(SHEET_FIELD, "").strip()
    if recorded and recorded != sheet.sha256:
        raise ValueError(f"{form_path} was built for another sheet ({recorded[:12]}…)")
    expected = {f"answer_{ref}" for ref in sheet.refs}
    found = (
        {name for name in texts if name.startswith("answer_")} if instrument.review else set(radios)
    )
    if found != expected:
        missing, extra = sorted(expected - found), sorted(found - expected)
        raise ValueError(f"{form_path} does not match {sheet_path}: {missing[:3]} {extra[:3]}")
    result = FilledForm(
        rows=[],
        initials=texts.get(INITIALS_FIELD, "").strip(),
        date=texts.get(DATE_FIELD, "").strip(),
        comment=texts.get(GENERAL_COMMENT, "").strip(),
    )
    for row in sheet.rows:
        ref = row["ref"]
        if instrument.review:
            written = texts[f"answer_{ref}"].strip()
            letters = [written] if written else []
        else:
            letters = _radio_value(document, radios[f"answer_{ref}"], instrument.letters)
        if len(letters) > 1:
            result.conflicts.append(ref)
        elif not letters:
            result.unanswered.append(ref)
        flag = "".join(f for f in ("U", "Q") if checks.get(f"flag{f}_{ref}"))
        result.rows.append(
            {
                **row,
                "answer": letters[0] if len(letters) == 1 else "",
                "flag": flag,
                "comment": texts.get(f"comment_{ref}", "").strip(),
            }
        )
    document.close()
    return result


def write_rows(rows: Sequence[dict[str, str]], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=PACKET_FIELDS)
        writer.writeheader()
        writer.writerows(rows)
