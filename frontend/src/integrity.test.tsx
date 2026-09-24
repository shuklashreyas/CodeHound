// @vitest-environment jsdom
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, expect, it } from "vitest";
import { IntegrityEvidence } from "./integrity";

afterEach(cleanup);
it("shows locations and review uncertainty alongside an incomplete inspection", () => {
  render(
    <IntegrityEvidence
      result={{
        status: "inconclusive",
        files_examined: 2,
        findings: [
          {
            kind: "assertion_changed_or_removed",
            scope: "test_answer",
            baseline_path: "tests/test_a.py",
            candidate_path: "tests/test_a.py",
            baseline_line: 7,
            candidate_line: 3,
            message: "An assertion changed.",
          },
        ],
        unverified: [
          {
            revision: "candidate",
            reason: "syntax_error",
            baseline_path: "test_b.py",
            candidate_path: "test_b.py",
          },
        ],
        limitations: ["Python only."],
      }}
    />,
  );
  expect(screen.getByText("tests/test_a.py:7")).toBeTruthy();
  expect(screen.getByText("tests/test_a.py:3")).toBeTruthy();
  expect(screen.getByText("Inspection incomplete")).toBeTruthy();
  expect(screen.getByText(/do not establish/)).toBeTruthy();
  expect(screen.queryByText("Pass")).toBeNull();
});
it("does not label unsupported changes as integrity passes", () => {
  render(
    <IntegrityEvidence
      result={{
        status: "not_applicable",
        files_examined: 0,
        findings: [],
        unverified: [],
        limitations: [],
      }}
    />,
  );
  expect(
    screen.getByText(
      /Other languages and custom test layouts remain unverified/,
    ),
  ).toBeTruthy();
  expect(screen.queryByText("Pass")).toBeNull();
});
