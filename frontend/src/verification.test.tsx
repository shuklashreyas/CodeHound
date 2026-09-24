// @vitest-environment jsdom
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { Verification } from "./verification";

const profile = {
  id: "url-contract",
  label: "URL contract",
  coverage: "Only URL parsing is checked.",
  visible_cases: 2,
  hidden_cases: 6,
};
const snapshot = {
  base_sha: "a".repeat(40),
  head_sha: "b".repeat(40),
  diff_sha256: "c".repeat(64),
  captured_at: "2026-09-23T00:00:00Z",
  files: [
    { filename: "src/urls.py", status: "modified", additions: 1, deletions: 1 },
  ],
  observations: [],
  diff: "-old\n+new",
};
const draft = {
  id: "v1",
  title: "Validate URLs",
  repository: "owner/repo",
  pr_url: "https://github.com/owner/repo/pull/1",
  issue_text: "Reject invalid URLs",
  status: "draft",
  snapshot: null,
  failure: null,
  checks: [
    {
      name: "task_completion",
      status: "not_run",
      explanation: "No evaluator has run this check.",
    },
  ],
};
const queued = {
  id: "j1",
  status: "queued",
  stage: "queued",
  profile,
  cancel_requested: false,
  created_at: "2026-09-23T00:00:00Z",
  base_sha: snapshot.base_sha,
  head_sha: snapshot.head_sha,
  diff_sha256: snapshot.diff_sha256,
  image_id: "sha256:image",
  assessment: null,
  failure: null,
  artifact: null,
};
function response(body: unknown, status = 200) {
  return Promise.resolve(new Response(JSON.stringify(body), { status }));
}
function harness(
  options: {
    report?: unknown;
    profiles?: unknown;
    jobs?: unknown[];
    detail?: unknown;
  } = {},
) {
  const mock = vi.fn((path: string) => {
    if (path.endsWith("/profiles"))
      return response(
        options.profiles || {
          configured: true,
          worker_online: true,
          profiles: [profile],
        },
      );
    if (path.endsWith("/executions")) return response(options.jobs || []);
    if (path.includes("/api/executions/"))
      return response(options.detail || queued);
    return response(options.report || draft);
  });
  vi.stubGlobal("fetch", mock);
  return mock;
}
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

it("captures an immutable snapshot before enabling execution", async () => {
  let captured = false;
  const fetch = vi.fn((path: string, options?: RequestInit) => {
    if (path.endsWith("/intake")) {
      expect(options?.method).toBe("POST");
      captured = true;
      return response({ ...draft, status: "ready", snapshot });
    }
    if (path.endsWith("/profiles"))
      return response({
        configured: true,
        worker_online: true,
        profiles: [profile],
      });
    if (path.endsWith("/executions")) return response([]);
    return response(captured ? { ...draft, status: "ready", snapshot } : draft);
  });
  vi.stubGlobal("fetch", fetch);
  render(<Verification id="v1" onUnauthorized={vi.fn()} />);
  await screen.findByText("Capture PR");
  expect(
    (screen.getByText("Run verification") as HTMLButtonElement).disabled,
  ).toBe(true);
  fireEvent.click(screen.getByText("Capture PR"));
  await screen.findByText("PR snapshot captured");
  expect(
    (screen.getByText("Run verification") as HTMLButtonElement).disabled,
  ).toBe(false);
  expect(screen.getByText("aaaaaaaaaaaa")).toBeTruthy();
  expect(screen.getByText("not run")).toBeTruthy();
});

it("keeps the idempotency key on an uncertain submission and exposes cancellation", async () => {
  const keys: string[] = [];
  let submitted = false;
  vi.stubGlobal(
    "fetch",
    vi.fn((path: string, options?: RequestInit) => {
      if (path.endsWith("/profiles"))
        return response({
          configured: true,
          worker_online: true,
          profiles: [profile],
        });
      if (path.endsWith("/executions") && options?.method === "POST") {
        keys.push(
          (options.headers as Record<string, string>)["Idempotency-Key"],
        );
        expect(JSON.parse(options.body as string)).toEqual({
          profile_id: profile.id,
        });
        if (keys.length === 1)
          return Promise.reject(new Error("lost connection"));
        submitted = true;
        return response(queued, 202);
      }
      if (path.endsWith("/executions"))
        return response(submitted ? [queued] : []);
      if (path.endsWith("/cancel"))
        return response({ ...queued, cancel_requested: true });
      if (path.includes("/api/executions/")) return response(queued);
      return response({ ...draft, status: "ready", snapshot });
    }),
  );
  render(<Verification id="v1" onUnauthorized={vi.fn()} />);
  await screen.findByText("Run verification");
  fireEvent.click(screen.getByText("Run verification"));
  await screen.findByRole("alert");
  fireEvent.click(screen.getByText("Run verification"));
  await screen.findByText("Cancel execution");
  expect(keys.length).toBe(2);
  expect(keys[0]).toBe(keys[1]);
  expect(
    (screen.getByText("Run verification") as HTMLButtonElement).disabled,
  ).toBe(true);
});

it("does not offer execution for an unsupported repository", async () => {
  harness({
    report: { ...draft, status: "ready", snapshot },
    profiles: { configured: true, worker_online: true, profiles: [] },
  });
  render(<Verification id="v1" onUnauthorized={vi.fn()} />);
  await screen.findByText(/No test profile is configured/);
  expect(
    (screen.getByText("Run verification") as HTMLButtonElement).disabled,
  ).toBe(true);
});

it("explains unavailable image and offline worker without manufacturing a verdict", async () => {
  harness({
    profiles: { configured: false, worker_online: false, profiles: [profile] },
  });
  render(<Verification id="v1" onUnauthorized={vi.fn()} />);
  await screen.findByText(/Execution image is not configured/);
  expect(screen.getByText(/Worker offline/)).toBeTruthy();
  expect(screen.queryByText("Improvement observed")).toBeNull();
});

it("shows an independent regression and baseline/candidate transitions", async () => {
  const comparison = {
    verdict: "regression_detected",
    reasons: [],
    counts: { regressions: 1, improvements: 1 },
    regressions: [{ nodeid: "empty_input", before: "passed", after: "failed" }],
    improvements: [
      { nodeid: "partial_page", before: "failed", after: "passed" },
    ],
    unresolved: [],
    unverified: [],
    unchanged_passes: [],
    missing_tests: [],
    added_tests: [],
  };
  const complete = {
    ...queued,
    status: "completed",
    assessment: {
      verdict: "regression_detected",
      signals: ["visible_improvement_with_independent_regression"],
    },
    artifact: {
      suites: {
        hidden: {
          baseline: { stdout: "<script>alert(1)</script>" },
          candidate: {},
          test_comparison: comparison,
        },
      },
      limitations: ["Only configured behavior checked."],
    },
  };
  harness({
    report: { ...draft, status: "ready", snapshot },
    jobs: [complete],
    detail: complete,
  });
  render(<Verification id="v1" onUnauthorized={vi.fn()} />);
  await screen.findByText("empty_input");
  expect(screen.getAllByText("Regression detected").length).toBe(2);
  expect(screen.getByText("partial_page")).toBeTruthy();
  expect(
    screen.getByText(/Full requirement adherence remains unverified/),
  ).toBeTruthy();
  expect(document.querySelector("script")).toBeNull();
});

it("refreshes authentication on an expired session", async () => {
  const refresh = vi.fn();
  vi.stubGlobal(
    "fetch",
    vi.fn(() => response({ detail: "Sign in again." }, 401)),
  );
  render(<Verification id="v1" onUnauthorized={refresh} />);
  await screen.findByRole("alert");
  expect(refresh).toHaveBeenCalledOnce();
});

it("aborts pending evidence when a report is unmounted", async () => {
  let finish!: (value: Response) => void;
  let signal: AbortSignal | undefined;
  vi.stubGlobal(
    "fetch",
    vi.fn((path: string, options: RequestInit) => {
      signal = options.signal as AbortSignal;
      if (path.endsWith("/profiles"))
        return response({
          configured: true,
          worker_online: true,
          profiles: [],
        });
      if (path.endsWith("/executions")) return response([]);
      return new Promise<Response>((resolve) => {
        finish = resolve;
      });
    }),
  );
  const view = render(<Verification id="v1" onUnauthorized={vi.fn()} />);
  view.unmount();
  expect(signal?.aborted).toBe(true);
  await act(async () => finish(new Response(JSON.stringify(draft))));
  expect(screen.queryByText("Validate URLs")).toBeNull();
});

it("requests the selected historical execution rather than displaying the latest result", async () => {
  const old = {
    ...queued,
    id: "old",
    status: "failed",
    failure: { code: "worker_lost", message: "Older worker stopped." },
  };
  const mock = harness({
    report: { ...draft, status: "ready", snapshot },
    jobs: [{ ...queued, status: "completed" }, old],
    detail: queued,
  });
  render(<Verification id="v1" onUnauthorized={vi.fn()} />);
  await screen.findByLabelText("Execution history");
  mock.mockImplementation((path: string) => {
    if (path.endsWith("/profiles"))
      return response({
        configured: true,
        worker_online: true,
        profiles: [profile],
      });
    if (path.endsWith("/executions"))
      return response([{ ...queued, status: "completed" }, old]);
    if (path.endsWith("/old")) return response(old);
    return response({ ...draft, status: "ready", snapshot });
  });
  fireEvent.change(screen.getByLabelText("Execution history"), {
    target: { value: "old" },
  });
  await screen.findByText("Older worker stopped.");
  await waitFor(() =>
    expect(
      mock.mock.calls.some(([path]) => path === "/api/executions/old"),
    ).toBe(true),
  );
});

it("polls an active job to completion and stops polling completed evidence", async () => {
  vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout"] });
  try {
    let complete = false;
    const mock = vi.fn((path: string) => {
      const current = { ...queued, status: complete ? "completed" : "running" };
      if (path.endsWith("/profiles"))
        return response({
          configured: true,
          worker_online: true,
          profiles: [profile],
        });
      if (path.endsWith("/executions")) return response([current]);
      if (path.includes("/api/executions/")) return response(current);
      return response({ ...draft, status: "ready", snapshot });
    });
    vi.stubGlobal("fetch", mock);
    await act(async () => {
      render(<Verification id="v1" onUnauthorized={vi.fn()} />);
    });
    expect(screen.getByText("Cancel execution")).toBeTruthy();
    complete = true;
    await act(async () => {
      await vi.advanceTimersByTimeAsync(2000);
    });
    expect(screen.queryByText("Cancel execution")).toBeNull();
    const calls = mock.mock.calls.length;
    await act(async () => {
      await vi.advanceTimersByTimeAsync(10_000);
    });
    expect(mock.mock.calls.length).toBe(calls);
  } finally {
    vi.useRealTimers();
  }
});

it("cancels the active execution and does not issue a passing verdict", async () => {
  let cancelled = false;
  vi.stubGlobal(
    "fetch",
    vi.fn((path: string, options?: RequestInit) => {
      if (path.endsWith("/cancel")) {
        expect(options?.method).toBe("POST");
        cancelled = true;
        return response({ ...queued, status: "cancelled" });
      }
      const current = { ...queued, status: cancelled ? "cancelled" : "queued" };
      if (path.endsWith("/profiles"))
        return response({
          configured: true,
          worker_online: true,
          profiles: [profile],
        });
      if (path.endsWith("/executions")) return response([current]);
      if (path.includes("/api/executions/")) return response(current);
      return response({ ...draft, status: "ready", snapshot });
    }),
  );
  render(<Verification id="v1" onUnauthorized={vi.fn()} />);
  fireEvent.click(await screen.findByText("Cancel execution"));
  await screen.findByText(
    "This execution was cancelled. No verdict was issued.",
  );
  expect(screen.queryByText("Cancel execution")).toBeNull();
  expect(screen.queryByText("Improvement observed")).toBeNull();
});
