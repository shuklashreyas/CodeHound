# Differential Python static analysis

`execution.static_analysis.analyze_static(snapshot, checkouts, image_id)` compares
the pinned baseline and candidate using Ruff **0.11.13**, installed from
[PyPI](https://pypi.org/project/ruff/0.11.13/) in the trusted Python test image.
Build the image after changes to `backend/test-environments/python/Dockerfile` and
configure its immutable `sha256:` image ID. An older image without this exact
Ruff version produces inconclusive evidence.

The trusted harness runs through the existing `ContainerRunner`: no network,
read-only repository mount and root filesystem, non-root user, no capabilities,
512 MiB memory, one CPU, 64 processes, and a 30-second deadline. Repository
Python source is never imported or executed for this analysis. Bounded regular
`.py` and `.pyi` files are copied into a fresh container temporary directory;
symlinks, special files and invalid paths are skipped with explicit coverage
notices. Only `.git` is excluded by the evaluator. Candidate Ruff configuration,
gitignore files, caches and `noqa` suppressions cannot control the scan.

The fixed command uses `--isolated --no-cache --no-fix --ignore-noqa
--no-respect-gitignore --select E,F --target-version py311 --line-length 100
--output-format json`. Preview rules are disabled. Ruff's fixed-version syntax
diagnostics are retained as `invalid-syntax`. No fixes are applied. These
settings are evaluator policy, so findings can differ from a project's lint CI.
[Ruff configuration documentation](https://docs.astral.sh/ruff/configuration/)
explains isolated configuration and rule selection.

The scan bounds are 2,000 source files, 20,000 directory entries, 1 MiB per file,
16 MiB source total, 2,000 findings, and 700,000 report bytes per revision.
Ruff subprocess output is drained with a fixed byte budget. The controller
also validates bounded JSON, rejecting duplicate keys, nonfinite values,
invalid nesting, unknown report fields, unknown file paths, invalid locations,
and unsupported rule codes. Tool version, fixed configuration SHA-256,
evaluator SHA-256, image ID, container execution metadata, scanned inventory,
skipped paths and limits remain in the artifact.

An artifact contains `baseline`, `candidate`, `new`, `resolved`, `existing`,
`counts`, `coverage`, `limits`, `tool`, `image_id`, `evaluator_sha256` and
`limitations`. Each finding records its relative path, rule, message, start/end
location, normalized source-line digest and a bounded display snippet. Existing
findings also retain the baseline location. Matching uses path, rule, message
and normalized source-line digest, ignoring line shifts. Captured file renames
are mapped before matching, and duplicate occurrences are paired by count.
Source edits can appear as resolved plus new findings; this is an explicit
matching limitation rather than a claim about runtime changes.

`status: completed` means both bounded scans completed without skipped coverage.
It may contain new findings, and is never a correctness verdict. Any timeout,
truncation, missing tool, evaluator change, invalid report or skipped source
makes the comparison `inconclusive`. Such artifacts retain per-revision
observations, but do not classify new or resolved findings. A completed scan
with no new E/F findings does not establish task adherence, runtime safety,
security, type correctness, or coverage of other languages.

Run `pytest tests/test_static_analysis.py` from `backend`. Set
`CODEHOUND_TEST_IMAGE_ID` to the newly built immutable trusted image ID to run
the real container differential test as well. Frontend evidence tests live in
`frontend/src/static-analysis.test.tsx`.
