// @vitest-environment jsdom
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, expect, it } from "vitest";
import { ImpactEvidence } from "./impact";

afterEach(cleanup);
it("shows an import trail as possible impact with explicit coverage limits", () => {
  render(
    <ImpactEvidence
      result={{
        status: "inconclusive",
        new_syntax_errors: [
          { path: "core.py", line: 3, kind: "new_syntax_error" },
        ],
        revisions: {
          baseline: {
            python_files: 3,
            parsed_files: 3,
            resolved_edges: 2,
            unresolved_imports: 1,
            ambiguous_modules: 0,
            affected_count: 1,
            affected: [
              {
                path: "api.py",
                distance: 1,
                import_chain: ["api.py", "core.py"],
                import_chain_truncated: false,
              },
            ],
            affected_truncated: false,
            excluded_directories: [".venv"],
          },
        },
        unverified: [
          { revision: "candidate", path: "core.py", reason: "syntax_error" },
        ],
        unverified_count: 1,
        limitations: [],
      }}
    />,
  );
  expect(screen.getByText("api.py → core.py")).toBeTruthy();
  expect(screen.getByText("core.py:3")).toBeTruthy();
  expect(
    screen.getByText(/They do not establish that a component is broken/),
  ).toBeTruthy();
  expect(
    screen.getByText("1 incomplete inspections or coverage limits"),
  ).toBeTruthy();
});
