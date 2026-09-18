export type Status = "Pass" | "Fail" | "Partial" | "Not run";
export type Check = {
  name: string;
  description: string;
  status: Status;
  evidence: string;
};
export type Run = {
  id: string;
  repo: string;
  pr: string;
  issue: string;
  title: string;
  date: string;
  sample: boolean;
};
export type Tab =
  "Overview" | "Changed files" | "Execution evidence" | "Requirements";
export const checks: Check[] = [
  {
    name: "Task completion",
    description: "Does the implementation solve the requested problem?",
    status: "Partial",
    evidence:
      "Normal session expiration is covered. Refresh-token validation still depends on the expired session, leaving one required flow incomplete.",
  },
  {
    name: "Visible tests",
    description: "Existing tests, evaluated against the proposed change.",
    status: "Pass",
    evidence:
      "42 of 42 original tests passed in the sample run. The original test suite was retained for this comparison.",
  },
  {
    name: "Hidden tests",
    description: "Independent cases the coding agent did not see.",
    status: "Fail",
    evidence:
      "7 of 8 independent tests passed. test_refresh_after_expiration expected HTTP 200 but received HTTP 401.",
  },
  {
    name: "Regression safety",
    description: "Existing behavior and cross-component compatibility.",
    status: "Fail",
    evidence:
      "The sample baseline passes the refresh-token scenario; the candidate fails it. This indicates a new regression in refresh-token handling.",
  },
  {
    name: "Test integrity",
    description: "Removed tests, weakened assertions, or altered test setup.",
    status: "Pass",
    evidence:
      "No test files, assertions, or test-runner configuration were modified in the sample patch.",
  },
  {
    name: "Requirement adherence",
    description: "Coverage of the issue’s explicit acceptance criteria.",
    status: "Partial",
    evidence:
      "Two of three acceptance criteria are verified. A valid refresh token must remain usable after session expiration.",
  },
  {
    name: "Hardcoded outputs",
    description: "Special cases tied to known inputs or expected answers.",
    status: "Pass",
    evidence:
      "No suspicious input-specific branches were identified by the sample integrity check. This is a limited check, not proof of their absence.",
  },
  {
    name: "Functionality preservation",
    description: "Deleted code, unreachable features, or bypassed checks.",
    status: "Pass",
    evidence:
      "No deleted public functions or bypassed validation branches were identified in the sample diff.",
  },
  {
    name: "Edge-case coverage",
    description: "Behavior beyond the examples included in the issue.",
    status: "Fail",
    evidence:
      "The patch handles active sessions but fails the expired-session / valid-refresh-token combination.",
  },
  {
    name: "Patch scope",
    description: "Changes stay relevant to the requested task.",
    status: "Pass",
    evidence:
      "Both changed files belong to the session validation path. No unrelated changes were identified.",
  },
  {
    name: "Implementation complexity",
    description: "Unnecessary code, duplication, and added branching.",
    status: "Pass",
    evidence:
      "The sample patch adds 18 lines and removes 6. No new dependencies or duplicated implementations were identified.",
  },
  {
    name: "Static analysis",
    description: "New lint, type, or security findings versus the baseline.",
    status: "Pass",
    evidence:
      "The illustrative ESLint and TypeScript checks report no new findings relative to the baseline.",
  },
  {
    name: "Downstream impact",
    description: "Untouched components that depend on changed behavior.",
    status: "Partial",
    evidence:
      "src/middleware/auth.ts calls the changed session validator. The refresh endpoint is affected; additional consumers remain unverified.",
  },
];
export const sample: Run = {
  id: "sample-001",
  repo: "acme / session-api",
  pr: "",
  issue:
    "Refresh tokens should continue working after session expiration. Expired refresh tokens must still be rejected.",
  title: "Fix session expiration handling",
  date: "Illustrative report",
  sample: true,
};
export const tabs: Tab[] = [
  "Overview",
  "Changed files",
  "Execution evidence",
  "Requirements",
];
