# Blinded two-model adjudication pilot — 2026-10-08

**The 50-patch run is complete: 100 accepted reviews, seven provisional correct
labels, and 45 cases requiring human review. No human reviews have been completed.**
These are AI reference judgments, not human ground truth or a measurement of
CodeHound's real-world accuracy.

## Method and retained corrections

Reviewer A used `gpt-6.1-sol`; reviewer B used `gpt-6-luna`. Every patch/reviewer
pair ran in a separate fresh CLI context with identical prompt and material
hashes. Accepted traces contain no tool calls. Inputs included the issue, patch,
pinned baseline source excerpts, and any independently collected neutral runtime
observations. CodeHound verdicts, probes, published resolution labels, earlier
AI notes, and other reviewers' answers were excluded. Models inspected supplied
runtime records; they did not independently execute code in their review sessions.

Nine patches had paired baseline/candidate issue-example executions; two had
explicitly unsupported execution artifacts and 39 had no runtime observations.
Thus evidence availability, not just model confidence, limits reference coverage.
The models share a provider and may have correlated errors or prior exposure to
these public tasks. Blinding is procedural and trace-audited, not an operating
system confidentiality boundary. See the [protocol](ai-adjudication.md).

Two baseline examples completed with task exceptions, but were initially marked
unavailable because their exit codes were nonzero. Completion and success are
different. After correcting availability metadata, both reviewers received each
corrected packet in new contexts. Raw runtime observations were unchanged, the
original reviews were preserved, and the replacement decision preceded comparison
of their answers. There were 104 completed sessions in total, with four superseded
responses and 100 final accepted reviews.

The original frozen protocol required High/High confidence. That strict trial
yielded one provisional label, selected for the human audit, leaving zero scoring
labels. A separately recorded `no_low` analysis matches the user's requested
High/Medium qualifying example. This policy correction occurred after responses
were available and is **not a preregistered analysis**. Answers, evidence gates,
failure-matching rules, sampling seed, and 20% audit rate were unchanged. Both
analyses are retained; neither policy writes human labels.

## Requested-policy results

| Measure | Result |
| --- | ---: |
| Patches / final reviews | 50 / 100 |
| Raw reviewer agreements / disagreements | 31 / 19 |
| Provisional correct / incorrect labels | 7 / 0 |
| Agreement labels selected for human audit | 2 |
| Eligible provisional scoring labels | 5 |
| Cases requiring human review | 45 |
| Completed human reviews | 0 |
| CodeHound decisions / abstentions across all patches | 23 / 27 |
| CodeHound coverage / abstention rate | 46% / 54% |
| Eligible reference-label coverage | 10% |

The seven provisional correct labels are SymPy 12096, 11618, and 12481, and
Requests 2317, 1921, 1142, and 2931. Reproducible sampling selected SymPy 12096
and Requests 1142 for human audit; they remain excluded from scoring pending review.

Human-review reasons overlap: 41 lack concrete behavioral evidence, 19 have
reviewer disagreement, 17 include an Unclear verdict, two await agreement audit,
and one lacks a shared demonstrated failure. Code-reading agreement alone did
not produce a reference label. The workflow did **not** reduce the human queue
to the hoped-for 10–20 cases.

## Conditional metrics and error analysis

The five eligible references are all provisionally correct, and CodeHound accepts
all five: TP=0, FP=0, FN=0, TN=5. Conditional FPR is 0/5; precision, recall, and
FNR are undefined because the relevant denominators are zero. Coverage within
this selected reference subset is 5/5, distinct from 23/50 overall coverage.

There are no scored false-positive or false-negative cases to analyze. This is
not evidence that CodeHound has no errors: no provisionally incorrect reference
qualified, 45 patches are excluded from scoring, and the reference labels are
unreviewed AI judgments. In particular, the audit and disagreement queue must
be resolved before making human-reviewed performance claims. Preserve model
answers when adding human decisions; never relabel an example merely to match
CodeHound's output.

## Local retained artifacts

Inputs are outside the product checkout at
`/Users/shreyas/CodeHound-review-inputs-2026-10-08/`:
`bundles-frozen-v2.json`, original bundles, `protocol.json`, neutral observations,
and `availability-correction.json`.

Final results are at
`/Users/shreyas/CodeHound-review-runs-v2-2026-10-08/final/`:

- `reviewer-a.json` and `reviewer-b.json`: all final responses.
- `review-source-manifest.json`: selected source records and hashes.
- `requested-policy.json`: explicit policy correction.
- `consensus-requested-policy.json`: seven provisional references and gate reasons.
- `reference-metrics-requested-policy.json`: conditional counts and denominators.
- `human-review-queue-requested-policy.json`: 45 cases without model verdict fields.
- `consensus.json` and `reference-metrics.json`: preserved stricter trial.

Original traces remain in `CodeHound-review-runs-2026-10-08`; replacement traces
remain in `CodeHound-review-runs-v2-2026-10-08`. These local runtime artifacts are
not bundled into Git. Reproduction requires the original corpus, packets, pinned
images and authenticated CLI described in the protocol. A rerun may yield different
model judgments even with identical inputs.

## Next evaluation work

1. Review the two randomly selected agreements and the disagreement/Unclear cases
   against the issue and neutral evidence, without showing CodeHound's verdict.
2. Collect additional task-level neutral observations for the 41 evidence-limited
   cases, then version inputs and rerun both reviewers independently.
3. Obtain adjudicated incorrect references before interpreting recall or FNR.
4. Report human-reviewed, provisional-reference, and unsupported populations
   separately; retain unsupported CodeHound outcomes as abstentions.
