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
import { App } from "./main";

vi.mock("./verification", () => ({
  Verification: ({ id }: { id: string }) => <div>Saved evidence {id}</div>,
}));
const saved = {
  id: "saved-one",
  repository: "owner/repo",
  pr_url: "https://github.com/owner/repo/pull/1",
  title: "Saved PR",
  issue_text: "Check the URL contract",
  created_at: "2026-09-23T00:00:00Z",
};
const session = {
  configured: true,
  user: { login: "octocat", name: "Octocat" },
};
function response(value: unknown, status = 200) {
  return Promise.resolve(new Response(JSON.stringify(value), { status }));
}
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  window.history.replaceState(null, "", "/");
});

it("restores a saved verification after refresh and removes its link for the sample", async () => {
  window.history.replaceState(null, "", "/?verification=saved-one");
  vi.stubGlobal(
    "fetch",
    vi.fn((path: string) =>
      response(path === "/api/auth/session" ? session : { items: [saved] }),
    ),
  );
  render(<App />);
  await screen.findByText("Saved evidence saved-one");
  fireEvent.click(screen.getByText("View sample"));
  expect(window.location.search).toBe("");
  expect(screen.queryByText("Saved evidence saved-one")).toBeNull();
  fireEvent.change(screen.getByLabelText("Select verification report"), {
    target: { value: "saved-one" },
  });
  expect(window.location.search).toBe("?verification=saved-one");
  expect(screen.getByText("Saved evidence saved-one")).toBeTruthy();
});

it("can restore an older owned verification outside the latest page", async () => {
  window.history.replaceState(null, "", "/?verification=saved-one");
  const fetch = vi.fn((path: string) =>
    response(
      path === "/api/auth/session"
        ? session
        : path === "/api/verifications/saved-one"
          ? saved
          : { items: [] },
    ),
  );
  vi.stubGlobal("fetch", fetch);
  render(<App />);
  await screen.findByText("Saved evidence saved-one");
  expect(
    fetch.mock.calls.some(([path]) => path === "/api/verifications/saved-one"),
  ).toBe(true);
});

it("does not restore saved data after sign-out while the list is pending", async () => {
  window.history.replaceState(null, "", "/?verification=saved-one");
  let finish!: (value: Response) => void;
  vi.stubGlobal(
    "fetch",
    vi.fn((path: string) => {
      if (path === "/api/auth/session") return response(session);
      if (path === "/api/auth/logout") return response({ ok: true });
      return new Promise<Response>((resolve) => {
        finish = resolve;
      });
    }),
  );
  render(<App />);
  fireEvent.click(await screen.findByText("Sign out"));
  await screen.findByText("Sign in with GitHub");
  await act(async () =>
    finish(new Response(JSON.stringify({ items: [saved] }))),
  );
  expect(screen.queryByText("Saved evidence saved-one")).toBeNull();
});

it("shows an owner-scoped restore error instead of displaying a different saved report", async () => {
  window.history.replaceState(null, "", "/?verification=someone-elses-record");
  vi.stubGlobal(
    "fetch",
    vi.fn((path: string) => {
      if (path === "/api/auth/session") return response(session);
      if (path.includes("?limit")) return response({ items: [saved] });
      return response({ detail: "Verification not found." }, 404);
    }),
  );
  render(<App />);
  await waitFor(() =>
    expect(
      screen.getByText(/Could not restore saved verification/),
    ).toBeTruthy(),
  );
  expect(screen.queryByText("Saved evidence saved-one")).toBeNull();
});
