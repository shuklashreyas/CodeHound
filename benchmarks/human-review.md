# Human correctness review protocol

The first 50 patches may be reviewed by one person, including the project author.
Describe that result as **single-reviewer, human-reviewed pilot ground truth**,
not expert consensus. AI-written notes and automated tests can assist investigation
but cannot serve as a human reviewer or provide human attestations.

## Fixed rubric

Judge each patch against its retained issue, original revision, changed code,
relevant surrounding behavior, and available execution evidence. Matching a
reference patch is not a requirement. Passing the selected tests is not sufficient
to establish complete correctness.

| Human judgment | Stored verdict | Criteria |
| --- | --- | --- |
| Correct | `valid` | Satisfies the task, preserves required existing behavior, and handles relevant edge cases. |
| Incorrect | `invalid` | Evidence establishes an incomplete fix, wrong behavior, regression, test manipulation, or task-inappropriate overfitting. |
| Unclear | `uncertain` | Evidence is insufficient, the task contract is ambiguous, or relevant behavior cannot be checked confidently. |

For an incorrect verdict, record at least one supported category:
`incomplete_fix`, `regression`, `task_mismatch`, `test_manipulation`, or `other`.
Use `other` with an explicit explanation where the stored categories do not fit.
A correct verdict has no failure categories. Avoid inferring intent from a
suspicious pattern alone: explain the concrete behavior or violated requirement.

The rationale should identify the requirement, source locations inspected,
reproduction or test evidence, expected and observed behavior, and remaining
uncertainty. A review based only on whether a diff looks plausible is incomplete.
Unclear cases must remain excluded from correctness-rate denominators.

## Independent first pass

1. Open the neutral packets in `data/ai-patch-review-2026-10-07/`. Each includes
   `issue.txt`, `patch.diff`, and `baseline.json` with the original commit.
2. Make the initial judgment before reading CodeHound verdicts, upstream outcomes,
   or AI assistance notes. The packets omit those fields, but source content can
   reveal identity; this is not guaranteed anonymity.
3. Inspect the relevant baseline and patched behavior. Record commands and evidence
   sufficient for another reviewer to understand the judgment. Execute repository
   code in an isolated environment rather than installing candidate code on the host.
4. Copy only personally completed entries from `reviews.template.json` into a
   separate review collection. Keep its case ID and identity hash unchanged.
   Include your name, timezone-qualified timestamp, verdict, rationale, categories,
   and the exact personal attestation required by the template.
5. Keep initial judgments intact when later consulting AI notes or evaluator
   results. Record any revised judgment and its evidence separately, identifying
   that the revision is no longer blind to those results.

The project author has already seen some evaluator findings in this session;
reviews of those cases cannot honestly be described as blinded. Preserve a note
about that exposure in the rationale and seek an independent second reviewer.
The intake schema does not currently have a dedicated blinding-status field.

## Second reviewer and disagreements

A second technically capable person should review the same identities independently,
using the same rubric and without seeing the first review or CodeHound decision.
Retain both original collections. Compare their initial judgments on the common
completed subset, reporting counts for agreement, disagreement, and uncertainty.
Any inter-rater statistic must state its population and treatment of unclear cases;
it is not yet computed by the current tool.

Resolve disagreements through an explicit evidence-based adjudication record.
Do not overwrite or silently discard dissenting initial reviews. The current
scorer excludes conflicting or uncertain reviews; appending a third verdict
does not override them. Formal adjudicated-label intake is future work, so keep
adjudications separate until that protocol is implemented and versioned.

One retained human review can currently produce an eligible pilot label. That
software rule does not establish independence, expertise, or reviewer identity:
attestation is self-reported. State the actual reviewer count and process beside
every reported accuracy figure.

## Score completed reviews

Save a collection containing only actual completed human reviews as
`data/ai-patch-pilot-2026-10-07/reviews.json`. The current empty collection is
intentional. AI assistance files must never be copied into it.

```bash
PYTHONPATH=backend/src backend/.venv/bin/python -m codehound.benchmark.task_report \
  --corpus data/ai-patch-pilot-2026-10-07/corpus.json \
  --manifest benchmarks/task-experiments-primary.json \
  --reviews data/ai-patch-pilot-2026-10-07/reviews.json \
  --output data/ai-patch-pilot-2026-10-07/task-level-human-reviewed-report.json
```

Use a new output filename for each revision. This revalidates retained execution
evidence before joining labels. Report the [conditional accuracy metrics](metrics.md)
with all-case coverage and reviewed coverage; do not label unsupported patches
correct or incorrect from an evaluator abstention.
