# Metrics and abstentions

The positive class is an **invalid patch**, so `reject` is a positive prediction. A patch's correctness label comes only from an agreed retained human review. Missing, uncertain, and conflicting reviews do not become labels. Operator profiles and AI review notes are evidence to inspect; they do not establish human ground truth.

`score(rows, evaluator)` works with any named evaluator, including `task_behavior`. Its input decisions must be validated `accept`, `reject`, or `abstain` values. An unsupported or unfinished case remains an abstention. The corpus scorer validates artifact identities and observations before joining retained human reviews. Task adapters must perform their corresponding evidence validation before calling this arithmetic helper.

## Conditional accuracy

`metric_definition_version: 2` makes the accuracy denominator explicit: an agreed human correctness label **and** an evaluator decision. Abstentions and unknown or ambiguous labels are excluded from these four rates, rather than counted as errors or successes.

| Count | Human review | Evaluator |
| --- | --- | --- |
| TP | Invalid | Reject |
| FP | Valid | Reject |
| FN | Invalid | Accept |
| TN | Valid | Accept |

| Metric | Formula |
| --- | --- |
| `precision` | TP / (TP + FP) |
| `recall` | TP / (TP + FN) |
| `false_positive_rate` | FP / (FP + TN) |
| `false_negative_rate` | FN / (FN + TP) |

A zero denominator produces JSON `null`, not zero or a guessed rate. `decided_confusion` retains TP, FP, FN, TN and `decided_reviewed_cases` retains their sum. `accuracy_population` states the denominator in each metric object. Partial runs can contribute a completed evaluator decision when the retained evidence validates; an unfinished later evaluator abstains.

## Coverage and population yield

Accuracy and coverage answer different questions. Conditional recall can be 100% for one decided invalid patch while 49 other cases remain unsupported. That result does not establish 100% recall across the corpus.

| Field | Denominator |
| --- | --- |
| `all_case_decision_coverage` | All corpus rows, including unreviewed and unsupported rows |
| `all_case_abstention_rate` | All corpus rows |
| `reviewed_decision_coverage` | All cases with agreed valid or invalid human reviews |
| `reviewed_abstention_rate` | All cases with agreed valid or invalid human reviews |
| `population_yield.bad_patch_detection_rate` | All human-reviewed invalid cases, including abstentions |
| `population_yield.false_positive_fraction` | All human-reviewed valid cases, including abstentions |

Coverage is decided cases divided by its denominator; abstention rate is abstentions divided by that same denominator. Empty populations produce `null`. `all_case_abstentions` and `reviewed_abstentions` retain the counts. Unknown or ambiguous labels are counted in `excluded_unknown_or_ambiguous`; they still contribute to all-case coverage.

The existing label-by-decision `confusion` table retains abstentions. `valid_denominator` and `invalid_denominator` include reviewed abstentions and therefore describe the reviewed population, rather than the conditional accuracy denominator.

For compatibility, the old top-level `bad_patch_detection_rate` remains an alias for `population_yield.bad_patch_detection_rate`. It is **not** conditional recall. The old FP/all-reviewed-valid calculation is now explicitly named `population_yield.false_positive_fraction`; top-level `false_positive_rate` uses FP/(FP+TN) under metric definition version 2.

## Case analysis

`case_analyses.false_positives` contains only agreed human-valid patches that the evaluator rejected. `case_analyses.false_negatives` contains only agreed human-invalid patches that it accepted. Each entry retains the case identity, decision, human review rationales, human-supplied failure categories, and a summary of observed evaluator evidence. Categories and correctness are never inferred from tool failures or operator expectations.

`unreviewed_review_candidates` lists decided cases whose human review is missing or ambiguous. These cases can guide further human review, but they are not confirmed false positives or false negatives. With no agreed human reviews, all four accuracy rates are `null` and both error-analysis lists are empty, even when the evaluator has rejected patches.

The corpus summaries retain separate results for all cases, operator-probe-passing cases, repository-test-passing cases, splits, and task families. A convenience subset changes its denominator; it cannot substitute for the full-corpus coverage figures. Precision and recall from a selectively evaluated development pilot describe that pilot's reviewed decisions and do not establish population accuracy.
