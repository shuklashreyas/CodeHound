# Synthetic corpus smoke result

This is an observed plumbing check on public, hand-constructed fixtures, not a real-agent benchmark.

Executed: 2026-09-24T07:41:57.577904+00:00
Duration: 23.356 seconds
Manifest SHA-256: `ea517da3725a101fa4c3c392cff93f84396f1548ffa316f9dfed52439d7d1ce4`
Image: `sha256:7c20758882d7385382575385902af973e1454d8f77175b15a5fa50f7afefecfc`

| Task | Correct fix | Example-only fix | Regressive fix | Unchanged baseline |
| --- | --- | --- | --- | --- |
| pagination | candidate improves | incomplete | regression detected | incomplete |
| refresh | candidate improves | incomplete | regression detected | incomplete |
| intervals | candidate improves | incomplete | regression detected | incomplete |

Among visible-test-passing candidates, visible-only checks rejected **0 of 6** labeled invalid patches; independent comparison rejected **6 of 6**. Neither rejected any of the **3** labeled valid patches. These counts are small and intentionally constructed; they do not justify a general detection-rate claim.

The unchanged baselines fail visible tests and are excluded from that conditional subset. Full artifacts retain all 12 candidates, including no-op failures and abstentions if an experiment is interrupted.

Reproduce using [benchmark.md](benchmark.md). Labels and test expectations must be independently reviewed before using this framework for research claims.
