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
it("renders bounded observations and keeps independent expected answers withheld", () => {
  render(
    <Requirements
      result={{
        source: "operator_profile",
        requirements: [
          {
            id: "name",
            description: "Reject invalid names",
            baseline: {
              status: "contradicted",
              passed: 0,
              failed: 1,
              unverified: 0,
            },
            candidate: {
              status: "supported_by_checks",
              passed: 1,
              failed: 0,
              unverified: 0,
            },
            cases: [
              {
                suite: "hidden",
                case_id: "trailing-newline",
                baseline: "failed",
                candidate: "passed",
                target: {
                  module: "packaging.utils",
                  function: "canonicalize_name",
                  source_directory: "src",
                },
                diagnostics: {
                  expectation: { visibility: "withheld", kind: "exception" },
                  baseline: {
                    status: "observed",
                    response: {
                      text: '{"kind":"returned","value":"demo\\n"}',
                      truncated: false,
                    },
                  },
                  candidate: {
                    status: "observed",
                    response: {
                      text: '{"kind":"raised","exception":"packaging.utils.InvalidName"}',
                      truncated: false,
                    },
                  },
                },
              },
            ],
          },
        ],
        unmapped_cases: [],
        limitations: [],
      }}
    />,
  );
  expect(screen.getByText(/Independent expected answer withheld/)).toBeTruthy();
  expect(screen.getByText("packaging.utils.canonicalize_name")).toBeTruthy();
  expect(
    screen.getByText(/"exception":"packaging.utils.InvalidName"/),
  ).toBeTruthy();
});
it("explains missing and truncated diagnostic responses", () => {
  render(
    <Requirements
      result={{
        source: "operator_profile",
        requirements: [
          {
            id: "one",
            description: "Compare values",
            baseline: {
              status: "unverified",
              passed: 0,
              failed: 0,
              unverified: 1,
            },
            candidate: {
              status: "contradicted",
              passed: 0,
              failed: 1,
              unverified: 0,
            },
            cases: [
              {
                suite: "visible",
                case_id: "one",
                baseline: "not_run",
                candidate: "failed",
                diagnostics: {
                  expectation: {
                    visibility: "shown",
                    text: '{"kind":"value","value":5}',
                    truncated: false,
                  },
                  baseline: { status: "unavailable" },
                  candidate: {
                    status: "observed",
                    response: {
                      text: '"<script>alert(1)</script>"',
                      truncated: true,
                    },
                  },
                },
              },
            ],
          },
        ],
        unmapped_cases: [],
        limitations: [],
      }}
    />,
  );
  expect(screen.getByText("No validated response available.")).toBeTruthy();
  expect(screen.getByText(/Preview truncated/)).toBeTruthy();
  expect(document.querySelector("script")).toBeNull();
});
