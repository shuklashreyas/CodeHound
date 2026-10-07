# Optional repository pytest evidence

An operator profile can add `repository_tests` alongside its independent JSON
function suites. HTTP callers choose a profile ID; they cannot supply repository
paths, commands, dependencies, or pytest arguments. Existing profiles remain valid
without this optional field.

```json
{
  "repository_tests": {
    "test_paths": ["tests/test_utils.py"],
    "source_directories": ["src"],
    "target_packages": ["packaging"],
    "timeout_seconds": 60
  }
}
```

This is a fragment of the operator profile. `test_paths` selects up to 20
repository-relative Python files or directories, with no absolute paths,
traversal, hidden components, or overlapping selections. The source directories
are repository-relative; `.` means the repository root. The timeout is between
1 and 120 seconds. Third-party dependencies must already be in the trusted local
image. CodeHound never imports repository Python or installs dependencies on the
host.

The evaluator copies the selected **baseline** tests and data once, preserving
their paths. It also freezes ancestor `conftest.py` and `__init__.py` files so
baseline fixtures and test package context can work. Both code revisions run
against this identical frozen tree. Candidate changes to tests, fixtures,
conftest, and pytest configuration do not replace these inputs. Candidate-added
tests are excluded from this evidence. Symlinks and non-regular files are
rejected. Inputs are limited to 10,000 files, 20,000 traversed entries, and 32 MiB;
hidden files and cache directories are excluded. The artifact records the
baseline revision, configuration hash, combined suite hash, and individual file
hashes and sizes.

Each revision runs offline as a non-root user in the same restricted Docker
execution boundary as the other evaluators. Code, frozen tests, and the trusted
harness are read-only. Pytest uses trusted configuration, disables automatic
plugin loading, and discovers conftest only within the frozen test tree. Tests
that rely on repository pytest settings, extra plugins, data outside the frozen
selection, or writable fixture directories may fail or remain inconclusive.

`target_packages` lists top-level Python package names whose imports must come
from the mounted revision. The harness clears those namespaces from the module
cache after loading trusted pytest, avoiding accidental reuse of installed
packages such as `packaging`. It then records the paths of all loaded target
modules. Missing target imports or paths outside `/workspace` make this evidence
inconclusive. Configure this field for every package the selected tests evaluate;
an empty list does not enforce target package import provenance.

The saved `repository_tests` artifact contains baseline and candidate execution
results, test transitions, provenance, and limitations. It is explicitly
`repository_controlled_in_process_pytest` evidence with
`affects_assessment: false`. Repository tests, baseline fixture code, and candidate
code share a Python process and can manipulate reports or import provenance.
Passing this suite never changes the independent verdict or establishes
requirement coverage. Missing inputs, collection failures, timeouts, missing
reports, and invalid import provenance cannot count as improvements.

For an operator-controlled CLI run, save only the nested configuration object
above as a JSON file and add `--repository-test-config /absolute/path/config.json`
to `python -m codehound.execution.verify`. The file is capped at 16 KiB. All usual
independent suite, image, and snapshot arguments are still required.
