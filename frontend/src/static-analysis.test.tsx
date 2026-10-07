// @vitest-environment jsdom
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { StaticAnalysisEvidence, type StaticAnalysis } from "./static-analysis";

afterEach(cleanup);

const revision = { status: "completed", files: [], findings: [], skipped_files: [], errors: [], source_bytes: 0 };
const result: StaticAnalysis = {
  status: "completed", image_id: "sha256:image", evaluator_sha256: "evaluator",
  tool: { name: "ruff", version: "0.11.13", rules: ["E", "F"], config_sha256: "config" },
  baseline: revision, candidate: revision, new: [], resolved: [], existing: [],
  counts: { new: 0, resolved: 0, existing: 0 },
  coverage: { baseline_files: 1, candidate_files: 1, excluded_directories: [".git"], comparison_complete: true },
  limits: { files: 2000 }, limitations: ["No type checking is performed."],
};

describe("differential static evidence", () => {
  it("states the bounded meaning of a completed comparison", () => {
    render(<StaticAnalysisEvidence result={result} />);
    expect(screen.getByText(/No new findings in the completed E\/F comparison/)).toBeTruthy();
    expect(screen.getByText(/runtime behavior remains unverified/)).toBeTruthy();
    expect(screen.getByText(/Ruff 0.11.13/)).toBeTruthy();
  });

  it("shows incomplete reports as inconclusive without a clean result", () => {
    render(<StaticAnalysisEvidence result={{ ...result, status: "inconclusive",
      coverage: { ...result.coverage, comparison_complete: false },
      candidate: { ...revision, status: "inconclusive", errors: ["Static container timeout; exit 137."] },
    }} />);
    expect(screen.getByRole("status").textContent).toContain("Static comparison inconclusive");
    expect(screen.queryByText(/No new findings in the completed/)).toBeNull();
    expect(screen.getByText("Static container timeout; exit 137.")).toBeTruthy();
  });

  it("renders source-controlled finding text safely with locations", () => {
    const item = { path: "subject.py", line: 3, column: 9, end_line: 3, end_column: 16,
      rule: "F821", message: "Undefined name `missing`", snippet: "<script>alert(1)</script>", source_sha256: "digest" };
    const { container } = render(<StaticAnalysisEvidence result={{ ...result, new: [item], counts: { ...result.counts, new: 1 } }} />);
    expect(screen.getByText("subject.py:3:9")).toBeTruthy();
    expect(screen.getByText("F821")).toBeTruthy();
    expect(screen.getByText("<script>alert(1)</script>")).toBeTruthy();
    expect(container.querySelector("script")).toBeNull();
  });
});
