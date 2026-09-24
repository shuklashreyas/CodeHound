import { useCallback, useEffect, useRef, useState } from "react";

type User = { login: string; name: string };
type Session = { configured: boolean; user: User | null };
type Repository = {
  id: number;
  full_name: string;
  description: string | null;
  language: string | null;
  url: string;
};
type Pull = { number: number; title: string; body: string; url: string };

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message);
  }
}

export async function api<T>(
  path: string,
  options?: RequestInit,
  timeoutMs = 20_000,
): Promise<T> {
  const timeout = AbortSignal.timeout(timeoutMs);
  const signal = options?.signal
    ? AbortSignal.any([options.signal, timeout])
    : timeout;
  let response: Response;
  try {
    response = await fetch(`/api${path}`, {
      ...options,
      credentials: "same-origin",
      signal,
    });
  } catch (error) {
    if (options?.signal?.aborted) throw error;
    throw new ApiError(
      timeout.aborted
        ? "The request timed out. Please try again."
        : "Could not reach CodeHound. Check that the backend is running and try again.",
      0,
    );
  }
  const data = await response.json().catch(() => null);
  if (!response.ok || !data) {
    throw new ApiError(
      typeof data?.detail === "string"
        ? data.detail
        : "CodeHound returned an unexpected response. Please try again.",
      response.status,
    );
  }
  return data as T;
}

export function useGitHub() {
  const [session, setSession] = useState<Session | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [signingOut, setSigningOut] = useState(false);
  const requestVersion = useRef(0);
  const refresh = useCallback(async () => {
    const version = ++requestVersion.current;
    setLoading(true);
    setError("");
    try {
      const next = await api<Session>("/auth/session");
      if (version === requestVersion.current) setSession(next);
    } catch (error) {
      if (version === requestVersion.current) {
        setSession(null);
        setError((error as Error).message);
      }
    } finally {
      if (version === requestVersion.current) setLoading(false);
    }
  }, []);
  useEffect(() => {
    void refresh();
    return () => {
      requestVersion.current++;
    };
  }, [refresh]);
  async function logout() {
    // Invalidate in-flight refreshes so they cannot restore a signed-out user.
    const version = ++requestVersion.current;
    setLoading(true);
    setSigningOut(true);
    setError("");
    try {
      await api("/auth/logout", {
        method: "POST",
        headers: { "X-CodeHound-Request": "1" },
      });
      if (version === requestVersion.current)
        setSession((previous) =>
          previous ? { ...previous, user: null } : null,
        );
    } catch (error) {
      if (version === requestVersion.current)
        setError((error as Error).message);
    } finally {
      if (version === requestVersion.current) {
        setLoading(false);
        setSigningOut(false);
      }
    }
  }
  return { session, error, loading, signingOut, refresh, logout };
}
type Auth = ReturnType<typeof useGitHub>;

export function GitHubAccount({
  auth,
  onConnect,
}: {
  auth: Auth;
  onConnect: () => void;
}) {
  return (
    <div className="github-account">
      {auth.error && (
        <button
          className="account-error"
          onClick={onConnect}
          title={auth.error}
        >
          Connection issue · details
        </button>
      )}
      {auth.session?.user ? (
        <>
          <span>@{auth.session.user.login}</span>
          <button
            className="button secondary"
            disabled={auth.loading}
            onClick={() => void auth.logout()}
          >
            {auth.signingOut ? "Signing out…" : "Sign out"}
          </button>
        </>
      ) : (
        <button className="button secondary" onClick={onConnect}>
          Sign in with GitHub <span aria-hidden="true">↗</span>
        </button>
      )}
    </div>
  );
}

export function githubLoginUrl(search = window.location.search) {
  const verification = new URLSearchParams(search).get("verification");
  const identifier =
    /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
  return (
    "/api/auth/github/login" +
    (verification && identifier.test(verification)
      ? `?verification=${encodeURIComponent(verification)}`
      : "")
  );
}

export function GitHubRepositories({
  auth,
  onSelect,
}: {
  auth: Auth;
  onSelect: (pull: Pull) => void;
}) {
  const [repos, setRepos] = useState<Repository[]>([]);
  const [pulls, setPulls] = useState<Pull[]>([]);
  const [selected, setSelected] = useState<Repository | null>(null);
  const [page, setPage] = useState(1);
  const [hasMore, setHasMore] = useState(false);
  const [query, setQuery] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [retry, setRetry] = useState(0);
  const [callbackError, setCallbackError] = useState(() => {
    const reason = new URLSearchParams(window.location.search).get(
      "auth_error",
    );
    const messages: Record<string, string> = {
      not_configured: "GitHub sign-in needs to be configured first.",
      invalid_state: "The sign-in could not be verified. Please try again.",
      expired: "The sign-in link expired. Please try again.",
      denied: "GitHub sign-in was canceled. You can try again below.",
      exchange_failed:
        "GitHub sign-in failed. Check the app configuration and try again.",
      unavailable: "Sign-in is temporarily unavailable. Try again later.",
    };
    return reason
      ? messages[reason] || "GitHub sign-in failed. Please try again."
      : "";
  });
  useEffect(() => {
    const url = new URL(window.location.href);
    if (url.searchParams.has("auth_error") || url.searchParams.has("github")) {
      url.searchParams.delete("auth_error");
      url.searchParams.delete("github");
      window.history.replaceState(
        null,
        "",
        url.pathname + url.search + url.hash,
      );
    }
  }, []);
  useEffect(() => {
    setRepos([]);
    setPulls([]);
    setSelected(null);
    setPage(1);
    setQuery("");
  }, [auth.session?.user?.login]);
  useEffect(() => {
    if (!auth.session?.user) return;
    const controller = new AbortController();
    setLoading(true);
    setError("");
    setHasMore(false);
    const path = selected
      ? `/github/repositories/${selected.full_name}/pulls?page=${page}`
      : `/github/repositories?page=${page}`;
    api<{ repositories?: Repository[]; pulls?: Pull[]; has_more: boolean }>(
      path,
      { signal: controller.signal },
    )
      .then((data) => {
        if (controller.signal.aborted) return;
        setRepos(data.repositories || []);
        setPulls(data.pulls || []);
        setHasMore(data.has_more);
      })
      .catch((error) => {
        if (!controller.signal.aborted) {
          setError(error.message);
          if (error instanceof ApiError && error.status === 401)
            void auth.refresh();
        }
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, [auth.session?.user?.login, selected, page, retry, auth.refresh]);
  function pickRepo(repo: Repository | null) {
    setSelected(repo);
    setPage(1);
    setQuery("");
    setRepos([]);
    setPulls([]);
  }
  return (
    <section className="panel github-panel">
      <div className="panel-heading">
        <div>
          <h3>{selected ? selected.full_name : "Your GitHub repositories"}</h3>
          <p className="muted">
            {selected
              ? "Choose an open pull request to create a verification draft."
              : "Connect your account to browse public repositories you have access to."}
          </p>
        </div>
        {auth.session?.user && (
          <span className="pill">@{auth.session.user.login}</span>
        )}
      </div>
      {callbackError && (
        <div className="github-error" role="alert">
          {callbackError}
          <button onClick={() => setCallbackError("")}>Dismiss</button>
        </div>
      )}
      {auth.error && (
        <div className="github-error" role="alert">
          {auth.error}
          <button onClick={() => void auth.refresh()}>Retry connection</button>
        </div>
      )}
      {auth.loading ? (
        <div className="empty-state" role="status">
          Checking GitHub connection…
        </div>
      ) : !auth.session?.user ? (
        <div className="github-connect">
          <div className="github-connect-icon" aria-hidden="true">
            ⌘
          </div>
          <h2>Your code. One connection.</h2>
          <p>
            Sign in with GitHub, find a repository, and choose the change you
            want CodeHound to inspect.
          </p>
          <a
            className={`button primary ${!auth.session?.configured ? "unavailable" : ""}`}
            href={auth.session?.configured ? githubLoginUrl() : undefined}
            aria-disabled={!auth.session?.configured}
          >
            Continue with GitHub ↗
          </a>
          <small>
            Public repositories only. No repository write permissions requested.
          </small>
          {auth.session && !auth.session.configured && (
            <details className="github-setup">
              <summary>GitHub sign-in needs setup</summary>
              <p>
                Register a GitHub OAuth app and configure the backend’s
                GITHUB_CLIENT_ID and GITHUB_CLIENT_SECRET. See the README for
                the callback URL and restart instructions.
              </p>
            </details>
          )}
        </div>
      ) : (
        <>
          <div className="github-toolbar">
            {selected && (
              <button
                className="button secondary"
                onClick={() => pickRepo(null)}
              >
                ← Repositories
              </button>
            )}
            <input
              className="search"
              aria-label={
                selected
                  ? "Search pull requests on this page"
                  : "Search GitHub repositories on this page"
              }
              placeholder={
                selected ? "Filter pull requests…" : "Filter this page…"
              }
              value={query}
              onChange={(e) => setQuery(e.target.value)}
            />
            <button
              className="button secondary"
              onClick={() => setRetry((r) => r + 1)}
              disabled={loading}
            >
              Refresh
            </button>
          </div>
          {loading ? (
            <div className="empty-state" role="status">
              {selected
                ? "Loading open pull requests…"
                : "Loading repositories from GitHub…"}
            </div>
          ) : error ? (
            <div className="github-error" role="alert">
              {error}
              <button
                onClick={() => {
                  setRetry((r) => r + 1);
                  void auth.refresh();
                }}
              >
                Try again
              </button>
            </div>
          ) : (
            <>
              {selected
                ? pulls
                    .filter((p) =>
                      `${p.title} ${p.number}`
                        .toLowerCase()
                        .includes(query.toLowerCase()),
                    )
                    .map((p) => (
                      <button
                        className="repository-row"
                        key={p.number}
                        onClick={() => onSelect(p)}
                      >
                        <span className="repo-icon">#{p.number}</span>
                        <span>
                          <strong>{p.title}</strong>
                          <small>Create verification draft</small>
                        </span>
                        <span className="repo-next">→</span>
                      </button>
                    ))
                : repos
                    .filter((r) =>
                      r.full_name.toLowerCase().includes(query.toLowerCase()),
                    )
                    .map((r) => (
                      <button
                        className="repository-row"
                        key={r.id}
                        onClick={() => pickRepo(r)}
                      >
                        <span className="repo-icon" aria-hidden="true">
                          ⌘
                        </span>
                        <span>
                          <strong>{r.full_name}</strong>
                          <small>
                            {r.description || "No description"}
                            {r.language ? ` · ${r.language}` : ""}
                          </small>
                        </span>
                        <span className="pill">PUBLIC</span>
                        <span>→</span>
                      </button>
                    ))}
              {(selected
                ? pulls.filter((p) =>
                    `${p.title} ${p.number}`
                      .toLowerCase()
                      .includes(query.toLowerCase()),
                  ).length
                : repos.filter((r) =>
                    r.full_name.toLowerCase().includes(query.toLowerCase()),
                  ).length) === 0 && (
                <div className="empty-state">
                  {query
                    ? "No matches on this page."
                    : selected
                      ? "No open pull requests on this page. You can still paste a PR URL using New verification."
                      : "No public repositories on this page."}
                </div>
              )}
            </>
          )}
          <div className="github-pagination">
            <button
              className="button secondary"
              disabled={loading || page === 1}
              onClick={() => {
                setPage((p) => p - 1);
                setQuery("");
              }}
            >
              Previous
            </button>
            <span>Page {page}</span>
            <button
              className="button secondary"
              disabled={loading || !hasMore}
              onClick={() => {
                setPage((p) => p + 1);
                setQuery("");
              }}
            >
              Next
            </button>
          </div>
          <div className="panel-foot">
            Repository and PR details come from GitHub. Selecting a PR creates a
            draft; it does not run verification.
          </div>
        </>
      )}
    </section>
  );
}
