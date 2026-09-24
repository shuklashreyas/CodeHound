# Structural test-integrity review

Queued executions now inspect changed Python test files from both pinned checkouts
before running behavior checks. The inspector uses Python's AST parser inside the
same restricted, offline Docker boundary. It reads source; it never imports tests,
runs their top-level statements, or loads candidate pytest configuration.

Findings include:

- Previously present test functions that disappear or are renamed.
- Assertion predicates or common unittest/pytest assertion calls that change or
  disappear. Assertion-message-only changes and moved lines are ignored.
- Newly added or changed explicit pytest/unittest skip and expected-failure markers.

GitHub's previous filename connects renamed files. Each finding includes baseline
and candidate paths, the relevant line, scope, and a review explanation. Existing
identical assertions are matched as a multiset, so repeated assertions are counted.
Added tests do not count as removed assertions. Public artifacts include hashes of
parsed source, the inspector/controller, and the immutable execution image.

These are **review hints**, not proof of weakened tests, malicious intent, or task
correctness. A legitimate refactor can produce findings. No findings is not an
integrity pass. Test paths, names and assertion forms are recognized heuristically;
custom discovery rules, aliases, generated tests and dynamic behavior can be missed.
Only Python is supported. Other changed test/configuration paths retain intake hints.

The dashboard exposes findings under **Structural test review** and updates the
**Test integrity** check. Behavioral improvement/regression results remain separate.
A parser failure never becomes a clean integrity result. Partial findings remain
visible alongside unverified files.

## Limits and reproduction

Each source file is capped at 1 MiB, with at most 8 MiB parsed per revision. Files
with more than 50,000 AST nodes or 2,000 inventory items are unverified. Duplicate
qualified function names are ambiguous. Symlinks are not followed. Container CPU,
memory, process and output limits apply, with a 30-second deadline per revision.
A timeout or malformed inspector report makes structural analysis inconclusive.

The worker enables inspection automatically for newly queued executions. Existing
artifacts are immutable and do not gain retrospective findings. To opt in from the
standalone comparison command, add `--inspect-tests`:

```sh
PYTHONPATH=backend/src backend/.venv/bin/python -m codehound.execution.verify \
  --mode independent --inspect-tests --snapshot data/pr-evidence.json \
  --visible-tests /absolute/path/to/profile.json \
  --image-id "$CODEHOUND_IMAGE_ID" --output data/inspected-comparison.json
```

The artifact's optional `test_integrity` object contains its status, findings,
unverified paths, source inventories and coverage limits. Older artifacts without
this object still render normally.
