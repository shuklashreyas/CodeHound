// @vitest-environment jsdom
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { GitHubAccount, GitHubRepositories, useGitHub } from "./github";

const signedIn = {
  configured: true,
  user: { login: "octocat", name: "Octocat" },
};
function response(body: unknown, status = 200) {
  return Promise.resolve(
    new Response(JSON.stringify(body), {
      status,
      headers: { "Content-Type": "application/json" },
    }),
  );
}
function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => {
    resolve = done;
  });
  return { promise, resolve };
}
function Harness() {
  const auth = useGitHub();
  return (
    <>
      <span data-testid="identity">
        {auth.session?.user?.login || "signed out"}
      </span>
      <span data-testid="loading">{String(auth.loading)}</span>
      <button onClick={() => void auth.refresh()}>Refresh session</button>
      <button onClick={() => void auth.logout()}>End session</button>
      <GitHubAccount auth={auth} onConnect={() => {}} />
    </>
  );
}
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("GitHub session handling", () => {
  it("ignores a stale refresh arriving after sign-out", async () => {
    const pending = deferred<Response>();
    vi.stubGlobal(
      "fetch",
      vi
        .fn()
        .mockImplementationOnce(() => response(signedIn))
        .mockImplementationOnce(() => pending.promise)
        .mockImplementationOnce(() => response({ ok: true })),
    );
    render(<Harness />);
    await waitFor(() =>
      expect(screen.getByTestId("identity").textContent).toBe("octocat"),
    );
    fireEvent.click(screen.getByText("Refresh session"));
    fireEvent.click(screen.getByText("End session"));
    await waitFor(() =>
      expect(screen.getByTestId("identity").textContent).toBe("signed out"),
    );
    await act(async () =>
      pending.resolve(new Response(JSON.stringify(signedIn))),
    );
    expect(screen.getByTestId("identity").textContent).toBe("signed out");
    expect(screen.getByTestId("loading").textContent).toBe("false");
  });
  it("surfaces backend connection failures outside the repository screen", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockRejectedValue(new TypeError("Failed to fetch")),
    );
    render(<Harness />);
    const error = await screen.findByRole("button", {
      name: "Connection issue · details",
    });
    expect(error.getAttribute("title")).toContain(
      "Check that the backend is running",
    );
    expect(screen.getByTestId("loading").textContent).toBe("false");
  });
  it("keeps the user signed in and exposes a failed sign-out", async () => {
    vi.stubGlobal(
      "fetch",
      vi
        .fn()
        .mockImplementationOnce(() => response(signedIn))
        .mockImplementationOnce(() =>
          response({ detail: "Invalid sign-out origin." }, 403),
        ),
    );
    render(<Harness />);
    await screen.findByText("@octocat");
    fireEvent.click(screen.getByText("End session"));
    const error = await screen.findByRole("button", {
      name: "Connection issue · details",
    });
    expect(error.getAttribute("title")).toBe("Invalid sign-out origin.");
    expect(screen.getByTestId("identity").textContent).toBe("octocat");
  });
  it("does not show validation-error objects as a message", async () => {
    vi.stubGlobal(
      "fetch",
      vi
        .fn()
        .mockImplementation(() => response({ detail: [{ msg: "bad" }] }, 422)),
    );
    render(<Harness />);
    const error = await screen.findByRole("button", {
      name: "Connection issue · details",
    });
    expect(error.getAttribute("title")).toContain("unexpected response");
  });
});

describe("Repository session expiry", () => {
  it("refreshes identity after GitHub rejects the session", async () => {
    const refresh = vi.fn().mockResolvedValue(undefined);
    vi.stubGlobal(
      "fetch",
      vi
        .fn()
        .mockImplementation(() => response({ detail: "Session expired" }, 401)),
    );
    render(
      <GitHubRepositories
        auth={{
          session: signedIn,
          loading: false,
          signingOut: false,
          error: "",
          refresh,
          logout: vi.fn(),
        }}
        onSelect={() => {}}
      />,
    );
    await waitFor(() => expect(refresh).toHaveBeenCalledOnce());
    expect(screen.getByRole("alert").textContent).toContain("Session expired");
  });
});

it("preserves a saved verification ID in the sign-in link without arbitrary destinations", async () => {
  const { githubLoginUrl } = await import("./github");
  const id = "ed4b9ad8-4529-4bd7-83cc-5268fd6dfdb9";
  expect(
    githubLoginUrl(`?verification=${id}&return_to=https://evil.example`),
  ).toBe(`/api/auth/github/login?verification=${id}`);
  expect(githubLoginUrl("?verification=https://evil.example")).toBe(
    "/api/auth/github/login",
  );
});
