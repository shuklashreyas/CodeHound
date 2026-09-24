// @vitest-environment jsdom
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, expect, it } from "vitest";
import { Requirements } from "./requirements";

afterEach(cleanup);
it("shows a requirement contradicted by an independent case without claiming issue coverage", () => {
  render(
    <Requirements
      result={{
        source: "operator_profile",
        requirements: [
          {
            id: "refresh",
            description: "Refresh tokens outlive sessions",
            baseline: {
              status: "supported_by_checks",
              passed: 1,
              failed: 0,
              unverified: 0,
            },
            candidate: {
              status: "contradicted",
              passed: 0,
              failed: 1,
              unverified: 0,
            },
            cases: [
              {
                suite: "hidden",
                case_id: "expired-session",
                baseline: "passed",
                candidate: "failed",
              },
            ],
          },
        ],
        unmapped_cases: [],
        limitations: [],
      }}
    />,
  );
  expect(screen.getByText("Contradicted by checks")).toBeTruthy();
  expect(screen.getByText("passed → failed")).toBeTruthy();
  expect(
    screen.getByText(/Coverage of your submitted issue remains unverified/),
  ).toBeTruthy();
});
it("explains missing requirement coverage", () => {
  render(
    <Requirements
      result={{
        source: "operator_profile",
        requirements: [],
        unmapped_cases: [{ suite: "visible", case_id: "one" }],
        limitations: [],
      }}
    />,
  );
  expect(
    screen.getByText("No requirements have been mapped to this profile."),
  ).toBeTruthy();
  expect(screen.getByText(/1 configured checks are not mapped/)).toBeTruthy();
});
