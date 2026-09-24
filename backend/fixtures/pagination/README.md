# Pagination verification fixture

Task: preserve the final partial page when splitting a list into pages. Empty input
returns no pages; nonpositive sizes raise `ValueError`. The issue's visible example
is `[1, 2, 3, 4, 5]` with size `2`.

- `original`: deliberately drops partial pages.
- `correct`: fixes the general case.
- `overfit`: special-cases the visible example but still drops other partial pages.
- `visible`: complete pages and the issue's example.
- `hidden`: independently varied page lengths, empty input, and invalid sizes.

These fixture tests are public in this repository for reproducibility. They stand
in for an independently held test set; they are not a secret benchmark. All three
implementations execute only inside the restricted Docker runner in the demo.

## Observed smoke-test results

Local Docker execution on 2026-09-24 UTC, using Python 3.12 and pytest 8.4.2:

| Implementation | Visible tests | Independent tests |
| --- | --- | --- |
| Original | 1 passed, 1 failed | 4 passed, 3 failed |
| Correct fix | 2 passed | 7 passed |
| Overfit fix | 2 passed | 4 passed, 3 failed |

The independent failures exercise partial pages at lengths/sizes `(7, 3)`,
`(1, 10)`, and `(10, 4)`. Both the original and overfit implementation lose items
in those cases. The correct implementation preserves every item.

This demonstrates the pipeline detecting one intentionally constructed failure
mode beyond visible tests. It does not estimate detection rates on real agent
patches. Reproduce it with the Docker demonstration command in the root README;
its JSON output retains the immutable image ID, logs, exit codes, and resource
limits for every run.
