# Blinded AI reference adjudication

This pipeline produces **provisional AI reference labels**, separate from both
CodeHound's judgments and actual human reviews. Two models can share errors;
agreement is not ground truth. A completed run may still route all 50 patches to
human review if behavioral evidence or confidence is insufficient.

## Information boundary

Inputs come from the existing 50 neutral review packets: the issue, candidate
patch, and pinned baseline identity. The packet builder downloads changed files
at those exact baseline commits and supplies numbered source excerpts around
changed hunks, plus imports. Excerpt limits and missing context are explicit.
Producer identity, published resolution labels, CodeHound profiles, probes,
verdicts, earlier AI notes, and other reviewers' answers are excluded.

A separate collector can add raw original/patched executions of issue-based
examples. Each artifact includes the executed source, disclosed adaptations,
stdout, stderr, exit status, image identity, and isolation settings. It contains
no expected-answer assertions or evaluator judgment. It uses the existing Docker
execution infrastructure, but does not execute CodeHound's behavioral probes.
Examples do not establish exhaustive correctness; offline Requests captures do
not prove actual network behavior. Missing or incomplete executions are explicit.

Reviewers A and B receive **identical prompt and bundle bytes**. Each patch and
reviewer gets a new `codex exec` session, an empty working directory, disabled
project instructions, ignored user configuration, and no resumed conversation.
The controller supplies the material inline. Reviewers must not call tools; any
logged tool invocation invalidates that response. The requested model, actual
session ID, prompt hash, material hash, raw model response, and trace hash are
recorded by the controller rather than trusted to the model.

This is audited procedural blinding, not an operating-system confidentiality
boundary. A read-only process can technically access other files or attempt
network access; such tool use must be rejected, not accepted as a blinded review.
Pretraining exposure to public tasks and correlated errors between models also
remain possible. The orchestrator has seen prior results; it does not supply
those results to reviewers or edit their judgments.

The initial reviewer models are `gpt-6.1-sol` and `gpt-6-luna`. They are different
models from the same provider, not independent human experts or cross-provider
adjudicators. The runner permits at most two simultaneous reviewer processes.
Fresh-session workflow is based on [official Codex documentation](https://learn.chatgpt.com/docs/agent-configuration/subagents)
and the installed CLI's supported non-interactive options.

## Required review and consensus

Every response contains:

- `verdict`: `Correct`, `Incorrect`, or `Unclear`.
- `confidence`: `High`, `Medium`, or `Low`; not a calibrated probability.
- Evidence linking requirements and observed behavior to delivered material IDs.
- Material actually inspected, what was verified, and remaining uncertainty.

The controller waits for the complete A/B inventory before comparing reviews.
Invalid, missing, stale, tool-using, or incomplete responses are not automatically
turned into labels. It never asks one reviewer to revise an answer after seeing
the other's answer.

| Condition | Result |
| --- | --- |
| Both Correct, both High, both cite recorded completed behavioral evidence | Provisional correct |
| Both Incorrect, both High, both identify the same failure and cite shared completed behavioral evidence | Provisional incorrect |
| Disagreement, either Unclear, or either below High | Human review |
| Agreement based only on code inspection, conjecture, or unavailable execution | Human review |
| A required issue/patch/code inspection is missing | Human review |

This pilot uses the conservative **High/High** threshold. Incorrect agreement
requires matching normalized failure descriptions and a shared runtime evidence
ID; semantically similar descriptions that do not match still go to review.
Evidence hashes verify delivery, not the truth of an AI's interpretation.

The seed and sample rate are frozen before reviewing. At least 20% of otherwise
qualifying agreements are selected by reproducible hash ranking for human audit.
Their provisional labels are retained, but they are excluded from scoring while
the audit is pending. The queue includes reasons for every required human review.
No human-review completion or personal attestation is generated automatically.

## Run the workflow

Use a new input directory outside the product repository. The collector and
packet builder refuse to replace frozen artifacts. These examples assume the
retained pilot corpus, original neutral packets, trusted dependency images, and
authenticated Codex CLI already exist.

```bash
PYTHONPATH=backend/src backend/.venv/bin/python -m codehound.benchmark.adjudication_packets \
  --corpus data/ai-patch-pilot-2026-10-07/corpus.json \
  --packets data/ai-patch-review-2026-10-07 \
  --output /absolute/path/review-inputs/packets

PYTHONPATH=backend/src backend/.venv/bin/python -m codehound.benchmark.neutral_examples \
  --inputs data/ai-patch-review-2026-10-07 \
  --output /absolute/path/review-inputs/neutral
```

`neutral_examples.py` is an explicitly scoped pilot collector for the listed
Requests and SymPy cases. It pins the locally retained image IDs; build compatible
trusted images before using it on another host. It is not universal test discovery.
Freeze neutral evidence into the delivered bundle without loading any judgments:

```python
from pathlib import Path
from codehound.benchmark.adjudication import read_list
from codehound.benchmark.adjudication_packets import attach_neutral
from codehound.benchmark.run import evidence_writer

root = Path('/absolute/path/review-inputs')
bundles = attach_neutral(
    read_list(root / 'packets/bundles-source-only.json', 'bundles'),
    root / 'neutral/manifest.json',
)
evidence_writer(root / 'bundles-frozen.json')({'schema_version': 1, 'bundles': bundles})
```

Keep reviewer outputs in a separate directory. Start with a small, separately
retained pilot subset to validate model availability and response shape, then
dispatch the complete cohort. Never change a bundle after either reviewer starts.

```bash
PYTHONPATH=backend/src backend/.venv/bin/python -m codehound.benchmark.adjudication_run \
  --bundles /absolute/path/review-inputs/bundles-frozen.json \
  --output /absolute/path/review-runs \
  --model-a gpt-6.1-sol --model-b gpt-6-luna --workers 2

PYTHONPATH=backend/src backend/.venv/bin/python -m codehound.benchmark.adjudication consensus \
  --bundles /absolute/path/review-inputs/bundles-frozen.json \
  --reviewer-a /absolute/path/review-runs/reviewer-a.json \
  --reviewer-b /absolute/path/review-runs/reviewer-b.json \
  --seed codehound-2026-10-08-frozen-v1 --sample-rate 0.2 \
  --output /absolute/path/review-runs/consensus.json
```

Per-case records permit reuse only when prompt, model, material, trace, and raw
answer still validate. Failed attempts retain logs when available and block
consensus. Preserve a failed attempt before making an explicitly recorded new
attempt; never choose an attempt based on whether its verdict is convenient.

## Join only after adjudication

The separate `reference-metrics` command regenerates consensus, validates retained
CodeHound execution through `task_report`, and joins by patch ID plus full patch
identity. It reports TP/FP/FN/TN, precision, recall, FPR, FNR, evaluator coverage,
reference-label coverage, and reference-subset abstentions. Zero denominators are
`null`. FP and FN lists refer only to this provisional reference population.

Use the consensus arguments above with `reference-metrics`, plus:

```bash
  --corpus data/ai-patch-pilot-2026-10-07/corpus.json \
  --manifest benchmarks/task-experiments-primary.json \
  --human-reviews data/ai-patch-pilot-2026-10-07/reviews.json
```

Human labels remain in the existing [human-review workflow](human-review.md).
The new pipeline never writes that file. Human overrides and audit completion
must be recorded as actual reviews, preserving the original AI answers; automatic
promotion of a pending AI reference after adjudication is not implemented.

Before human audits are complete, describe results as **two-model blinded AI
adjudication with human review pending**. Only after actual review may a report say
“with human review of disagreements, uncertain cases, and a random agreement
sample.” Report the number reviewed and the remaining queue rather than assuming
the process reduces 50 cases to a predetermined workload.
