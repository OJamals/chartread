# ChartRead — TRIPOD-LLM checklist

Gallifant et al., "The TRIPOD-LLM reporting guideline for studies using large language models",
*Nature Medicine* 31:60–69 (2025), Table 2 (19 main items, 50 subitems). This is the open item
"TRIPOD-LLM checklist" in `chartread-standards.md`. Completed 2026-10-18 against
`docs/paper/chartread.tex`.

**Design and task.** ChartRead evaluates existing models (research design **E**). It is not a
deployment in a healthcare setting (**H**), and no model is developed or tuned (**M**, **D**). The
task is binary classification (**C**). TRIPOD-LLM marks an item by the designs and tasks it applies
to. Items for other designs or tasks are listed as *not applicable*. Healthcare-setting (**H**)
items are answered anyway where the answer is short.

Status: **reported** (where), **partial** (what is missing), **pending** (blocked on the author or
the raters), **n/a**.

| Item | Topic | Applies | Status | Where / what is missing |
|---|---|---|---|---|
| 1 | Title: evaluation, task, population, outcome | All | partial | The current title names the task and outcome but not that this is an evaluation of existing models. The recommended titles do (owner's choice pending). |
| 2 | Abstract (TRIPOD-LLM for abstracts) | All | partial | Reported: background, objectives, data, tasks, evaluation, results, implications. Missing 2f: models are named as "frontier"/"open-weight", not by name and version (Appendix A). |
| 3a | Healthcare context, rationale, existing approaches | All | reported | Introduction; Related work. |
| 3b | Target population, intended use, users | E H | partial | Introduction: pipelines that read notes for retrieval, summaries and problem lists. No intended end user is named; it is a benchmark, not a tool. |
| 4 | Objectives: development, tuning or validation | All | reported | Introduction (evaluation of existing models). |
| 5a | Data sources and rationale | All | reported | Benchmark: constructed stratum, NegEx/ConText; addendum 2: MTSamples. |
| 5b | Data points and distribution | All | reported | Table 1 (design); real-stratum mapping counts; addendum 2 unit counts. |
| 5c | Dates of the oldest and newest text | All | reported | Benchmark, "Real stratum" paragraph, from 2026-10-18. NegEx/ConText reports, about 2001–2009 (dates not published per row); MTSamples, scraped about 2018; constructed items generated October 2026. |
| 5d | Pre-processing and quality checks | All | reported | Real-stratum mapping and exclusions; MTSamples line restoration (addendum 2); post hoc item audit (deviation 10). |
| 5e | Missing or imbalanced data, omissions | All | reported | Excluded classes and balancing of affirmed items; dropped rows (`AUDIT_REAL`); unanswered replies count in every denominator. |
| 6a | Model name, version, last training date | All | partial | Appendix A gives identifiers as served. Training cut-off dates are not reported: the providers do not publish them for most identifiers. The paper says so (Appendix A, from 2026-10-18). |
| 6b | Development, architecture, alignment | M D | n/a | No model developed. |
| 6c | Generation details: prompts, inference settings | M D E | reported | Task paragraph (prompt verbatim); Appendix A (temperature, 2,048 tokens, one sample; no seed is settable through the route). |
| 6d | Initial and post-processed output | All | reported | Free text ending "Answer: A/B", read by a fixed parser; an uncommitted reply is unanswered. |
| 6e | Classification details, thresholds | All (C) | reported | Binary letter; no probabilities or thresholds. |
| 7a | Generative-output quality metrics | QA IR DG SS MT | n/a | Classification task. |
| 7b | Metric relevance to the downstream task | E H | reported | Discussion: false affirmation is a false clinical assertion in a problem list, summary or retrieved passage. |
| 7c | Outcome definition, how predictions were obtained, inference dates | E H | reported | Outcomes paragraph; Appendix A (API through one proxy; run dates 17–18 October 2026). |
| 7d | Subjective assessment: assessors, instructions, agreement | All | pending | Label validation is pre-registered and the sheets are ready (`datasets/eval/chartread_v1/validation/`). Results are pending. |
| 7e | Comparison to other models, humans, standards | All | reported | Eleven arms plus a rule-based engine; clinician agreement pending. |
| 8a | Annotation guidelines | All | partial | Constructed labels come from templates and real labels from the corpus authors; rater instructions are in the validation README. Not yet summarised in the paper. |
| 8b | Number of annotators, overlap, agreement | All | pending | Two raters on 200 constructed items (full overlap), one on 100 real items, Cohen's κ; results pending. |
| 8c | Annotator background | All | pending | Clinicians; names and training to be reported with the results. |
| 9a | Prompt design, curation, selection | All | reported | One prompt fixed in the pre-registration; no tuning. The bare-options prompt is a post hoc variant, reported as such. |
| 9b | Data used to develop prompts | All | reported | None. The pilot (deviation 1) tested the gate, not the wording. |
| 10 | Pre-processing before summarisation | SS | n/a | |
| 11 | Instruction tuning or alignment | M D | n/a | |
| 12 | Compute | M D E | reported | Appendix A: hosted APIs only (no local GPU), with the number of requests and summed request time. The rule engine runs on one CPU. |
| 13 | Ethics approval | All | pending | Declarations: institutional determination for the clinician rating pending. |
| 14a | Funding | All | pending | Declarations. |
| 14b | Conflicts of interest | All | pending | Declarations. |
| 14c | Protocol access | H | reported | The pre-registration and addenda are released with the code. |
| 14d | Registration | H | reported | Not registered in a public registry; the protocol's SHA-256 was recorded before the pilot (Validation section). |
| 14e | Data availability | All | reported | Declarations: github.com/OJamals/chartread (private until the clinician ratings are returned). Real-text responses as answer letters only. |
| 14f | Code availability | All | reported | Declarations: github.com/OJamals/chartread, Apache-2.0. |
| 15 | Patient and public involvement | H | reported | Declarations: none. |
| 16a | Flow of text through the study | E H | reported | Real-stratum mapping counts (kept and dropped by reason); addendum 2 census of units. |
| 16b | Characteristics per source and split | E H | reported | Report types (discharge, radiology, echo, colonoscopy); MTSamples specialties are not broken down. |
| 16c | Clinical-variable comparison | E H | n/a | No clinical outcome. |
| 16d | Participants and outcome events per analysis | E H | reported | Every rate gives its numerator and denominator. |
| 17 | Performance on pre-specified metrics | All | reported | Results: FAR and FDR with Wilson intervals; pre-registered tests with Holm. |
| 18 | Model updating | All | n/a | No model updated. Benchmark versioning (v1.1) is reported separately. |
| 19a | Interpretation, including fairness | All | partial | Interpretation in Discussion. Fairness: the items carry no patient demographics apart from the sex-concordance fix in v1.1, so subgroup fairness is not measurable here. Stated in Limitations. |
| 19b | Limitations | All | reported | Limitations paragraph. |
| 19c | Known data challenges: representation, missingness | E H | reported | Limitations: synthetic, English and short constructed text; single-sentence real rows; one public note corpus. |
| 19d | Intended use, input, user, autonomy | E H | partial | The benchmark's intended use is pre-deployment testing of note-reading pipelines; no deployed system is evaluated. |
| 19e | Handling poor or unavailable input | E H | reported | The main result: a window without its header; the "cannot tell" addenda; the section-path prefix. |
| 19f | User interaction and expertise | E H | n/a | No users. |
| 19g | Next steps | All | reported | Discussion and Version 1.1 (v1.2; MIMIC-IV exposure pending credentialing). |

**Changes made to the paper for this checklist (2026-10-18):** text dates (5c), training-date
statement (6a), compute (12), patient and public involvement (15), registration statement (14d).
Remaining gaps: the title and abstract wording (1, 2) wait on the owner's title choice, and 7d,
8b, 8c, 13, 14a and 14b wait on the raters and the author.
