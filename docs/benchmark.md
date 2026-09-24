# Reproducible verification experiments

The offline benchmark runner compares **visible tests only** with CodeHound's
**independent before/after evaluator**. Labels are used for scoring after execution;
they are never sent to candidate containers or used to choose an evaluator verdict.

The included corpus contains three small, public, hand-constructed tasks with four
candidate patches each: a general fix, an example-only fix, a regression-inducing
fix, and an unchanged baseline. Tasks cover pagination, refresh-token expiration,
and half-open interval merging. Labels follow their explicit function contracts.
These are synthetic demonstrations, not samples of real coding agents. Success on
these intentionally designed cases is not an estimate of real-world detection.

## Run

Build the trusted image using the root README, then run:

```sh
PYTHONPATH=backend/src backend/.venv/bin/python -m codehound.benchmark.run \
  --manifest backend/fixtures/benchmark.json \
  --image-id "$CODEHOUND_IMAGE_ID" \
  --output data/benchmark.json --max-seconds 600
```

The runner executes each task's baseline once per suite, then each candidate against
exactly the same frozen profiles. Expected answers remain outside the containers.
Artifacts include task specifications, label rationales, image/profile/source/
manifest hashes, case evidence, comparisons, decisions, metrics and timestamps.

Evidence is checkpointed atomically during the experiment. A new run refuses an
existing output path. Deadline expiry preserves completed and partial evidence;
unfinished decisions remain abstentions. Cancellation saves a final cancelled
checkpoint before propagating cancellation. A hard kill may leave the last
checkpoint marked running: do not call it a completed experiment. Changed source
identities invalidate all decisions, rather than retaining a misleading aggregate.

## Decisions and denominators

| Evaluator | Accept | Reject | Abstain |
| --- | --- | --- | --- |
| Visible only | Every candidate visible check passes | Valid visible report contains a failed assertion | Missing, invalid, skipped, or errored evidence |
| Independent | Improvement observed with no configured regressions or unresolved failures | Regression detected or incomplete implementation | Inconclusive or no behavior change observed |

Acceptance refers only to configured contracts, not the entire software task.
The runner measures:

- **Invalid detection:** rejected invalid candidates / all labeled invalid
  candidates. Abstentions remain in the denominator.
- **False positives:** rejected valid candidates / all labeled valid candidates.
- **Decision coverage:** non-abstained labeled candidates / all labeled candidates.
- **Visible-passing subset:** the same metrics restricted to candidates accepted by
  the visible-only baseline. A timeout or skipped test is not visible success.

Confusion counts include `unreviewed` labels, but these are excluded from accuracy
rates. Undefined rates are JSON `null`, never a manufactured zero. Results are
patch-weighted; related candidate patches are correlated, so these counts do not
provide statistical confidence intervals. No calibrated confidence is produced.
Training, evaluation and demo splits also receive separate metric groups.

## New datasets

Manifests are operator-owned JSON with `name`, `dataset_kind`, `provenance` and
`tasks`. Each task supplies `id`, `family`, `split`, `issue`, `baseline`,
`visible_profile`, `independent_profile`, and `candidates`. A candidate supplies
`id`, `workspace`, `label` (`valid`, `invalid`, or `unreviewed`), and `label_reason`.
Paths are relative to the manifest directory; escapes and symlinks are rejected.
Workspaces are bounded, fingerprinted directories. The existing
[JSON profile contract](independent-evaluator.md) applies to both suites.

Related tasks must have the same `family` and stay in one split; duplicate task or
candidate identities are rejected. A synthetic dataset must use the demo split.
A `human_reviewed` label is the operator's assertion of provenance, not something
the runner can independently certify. Keep review records, independent label
criteria, and held-out benchmark checks separate from verifier development.
