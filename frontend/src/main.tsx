import { StrictMode, useEffect, useState } from "react";
import type { FormEvent, ReactNode } from "react";
import { createRoot } from "react-dom/client";
import "./styles.css";
import { GitHubAccount, GitHubRepositories, useGitHub, api } from "./github";

import { checks, sample, tabs } from "./report-data";
import type { Status, Run, Tab } from "./report-data";

function Icon({ name, size = 18 }: { name: string; size?: number }) {
  const paths: Record<string, ReactNode> = {
    grid: (
      <>
        <rect x="3" y="3" width="7" height="7" rx="1.5" />
        <rect x="14" y="3" width="7" height="7" rx="1.5" />
        <rect x="3" y="14" width="7" height="7" rx="1.5" />
        <rect x="14" y="14" width="7" height="7" rx="1.5" />
      </>
    ),
    branch: (
      <>
        <circle cx="6" cy="5" r="2" />
        <circle cx="6" cy="19" r="2" />
        <circle cx="18" cy="5" r="2" />
        <path d="M6 7v10m12-10v3c0 4-12 2-12 7" />
      </>
    ),
    shield: (
      <>
        <path d="m12 3 8 3v6c0 5-8 9-8 9s-8-4-8-9V6l8-3Z" />
        <path d="m8 12 3 3 5-6" />
      </>
    ),
    file: (
      <>
        <path d="M14 3H5v18h14V8l-5-5Z" />
        <path d="M14 3v5h5M8 12h8M8 16h5" />
      </>
    ),
    arrow: <path d="M5 12h14m-5-5 5 5-5 5" />,
    plus: <path d="M12 5v14M5 12h14" />,
    down: (
      <>
        <path d="M12 3v12m-5-5 5 5 5-5M4 16v5h16v-5" />
      </>
    ),
    clock: (
      <>
        <circle cx="12" cy="12" r="9" />
        <path d="M12 7v5l3 2" />
      </>
    ),
    chevron: <path d="m9 5 7 7-7 7" />,
    check: <path d="m5 12 4 4L19 6" />,
    close: <path d="m6 6 12 12M6 18 18 6" />,
    info: (
      <>
        <circle cx="12" cy="12" r="9" />
        <path d="M12 11v6M12 7v1" />
      </>
    ),
    github: (
      <>
        <path d="M9 19c-5 1-5-3-7-3m14 6v-4c0-1-.3-2-1-2 4-.5 6-2 6-6 0-2-.6-3-2-4 .3-1 .3-3-.3-4-2 0-3 1-4 2-2-.5-4-.5-6 0-1-1-2-2-4-2-.6 1-.6 3-.3 4-1.4 1-2 2-2 4 0 4 2 5.5 6 6-.7 0-1 1-1 2v4" />
      </>
    ),
  };
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.6"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      {paths[name] || paths.file}
    </svg>
  );
}
function Badge({ status }: { status: Status }) {
  return (
    <span className={`badge ${status.toLowerCase().replace(" ", "-")}`}>
      <span>
        {status === "Pass"
          ? "✓"
          : status === "Fail"
            ? "×"
            : status === "Partial"
              ? "◐"
              : "−"}
      </span>
      {status}
    </span>
  );
}
function Logo() {
  return (
    <div className="brand">
      <div className="logo-crop">
        <img src="/codehound-logo.png" alt="" />
      </div>
      <span>
        CodeHound<span className="brand-dot">.</span>
      </span>
    </div>
  );
}
type SavedDraft = {
  id: string;
  repository: string;
  pr_url: string;
  issue_text: string;
  title: string;
  created_at: string;
};
function savedRun(draft: SavedDraft): Run {
  return {
    id: draft.id,
    repo: draft.repository.replace("/", " / "),
    pr: draft.pr_url,
    issue: draft.issue_text,
    title: draft.title,
    date: new Date(draft.created_at).toLocaleString(),
    sample: false,
    persisted: true,
  };
}
function App() {
  const auth = useGitHub();
  const [page, setPage] = useState(() => {
    const query = new URLSearchParams(window.location.search);
    return query.has("github") || query.has("auth_error")
      ? "Repositories"
      : "Verifications";
  });
  const [run, setRun] = useState<Run>(sample);
  const [runs, setRuns] = useState<Run[]>([]);
  const [tab, setTab] = useState<Tab>("Overview");
  const [filter, setFilter] = useState("All checks");
  const [expanded, setExpanded] = useState<string | null>(null);
  const [modal, setModal] = useState(false);
  const [url, setUrl] = useState("");
  const [issue, setIssue] = useState("");
  const [error, setError] = useState("");
  const [query, setQuery] = useState("");
  const [toast, setToast] = useState("");
  const [saving, setSaving] = useState(false);
  useEffect(() => {
    const controller = new AbortController();
    if (!auth.session?.user) {
      setRuns((previous) => previous.filter((item) => !item.persisted));
      setRun((previous) => (previous.persisted ? sample : previous));
      return () => controller.abort();
    }
    api<{ items: SavedDraft[] }>("/verifications?limit=100", {
      signal: controller.signal,
    })
      .then((data) => {
        if (!controller.signal.aborted)
          setRuns((previous) => [
            ...data.items.map(savedRun),
            ...previous.filter((item) => !item.persisted),
          ]);
      })
      .catch((error) => {
        if (!controller.signal.aborted)
          setToast(`Could not load saved drafts: ${error.message}`);
      });
    return () => controller.abort();
  }, [auth.session?.user?.login]);
  const currentChecks = checks.map((c) =>
    run.sample
      ? c
      : {
          ...c,
          status: "Not run" as Status,
          evidence:
            "No execution evidence is available. The verification runner has not evaluated this submission.",
        },
  );
  function chooseRun(next: Run) {
    setRun(next);
    setPage("Verifications");
    setTab("Overview");
    setFilter("All checks");
    setExpanded(null);
  }
  async function submit(event: FormEvent) {
    event.preventDefault();
    let parsed: URL;
    try {
      parsed = new URL(url.trim());
    } catch {
      setError("Enter a valid public GitHub pull request URL.");
      return;
    }
    const match = parsed.pathname.match(
      /^\/([\w.-]+)\/([\w.-]+)\/pull\/([1-9]\d*)\/?$/,
    );
    if (
      parsed.protocol !== "https:" ||
      parsed.hostname !== "github.com" ||
      parsed.port ||
      parsed.username ||
      parsed.password ||
      !match
    ) {
      setError("Use https://github.com/owner/repository/pull/123.");
      return;
    }
    if (issue.trim().length < 10) {
      setError(
        "Add a task description with at least 10 non-padding characters.",
      );
      return;
    }
    let next: Run = {
      id: crypto.randomUUID(),
      repo: `${match[1]} / ${match[2]}`,
      pr: `https://github.com/${match[1]}/${match[2]}/pull/${match[3]}`,
      issue: issue.trim(),
      title: `Pull request #${match[3]}`,
      date: new Date().toLocaleString(),
      sample: false,
    };
    if (saving) return;
    if (auth.session?.user) {
      setSaving(true);
      try {
        next = savedRun(
          await api<SavedDraft>("/verifications", {
            method: "POST",
            headers: {
              "Content-Type": "application/json",
              "X-CodeHound-Request": "1",
            },
            body: JSON.stringify({ pr_url: next.pr, issue_text: next.issue }),
          }),
        );
      } catch (error) {
        setError((error as Error).message);
        return;
      } finally {
        setSaving(false);
      }
    }
    setRuns((previous) => [
      next,
      ...previous.filter((item) => item.id !== next.id),
    ]);
    chooseRun(next);
    setModal(false);
    setUrl("");
    setIssue("");
    setError("");
  }
  function exportReport() {
    const blob = new Blob(
      [
        JSON.stringify(
          {
            ...run,
            executionStatus: run.sample ? "illustrative_sample" : "not_run",
            confidence: null,
            checks: currentChecks,
          },
          null,
          2,
        ),
      ],
      { type: "application/json" },
    );
    const link = document.createElement("a");
    const address = URL.createObjectURL(blob);
    link.href = address;
    link.download = `codehound-${run.id}.json`;
    link.click();
    URL.revokeObjectURL(address);
    setToast("Report exported as JSON.");
  }
  return (
    <div className="app-shell">
      <aside className="sidebar" inert={modal}>
        <Logo />
        <div className="workspace">
          <span className="workspace-icon">C</span>
          <div>
            Personal workspace<small>Development</small>
          </div>
          <span className="workspace-chevron">⌄</span>
        </div>
        <div className="nav-label">WORKSPACE</div>
        <nav aria-label="Main navigation">
          {[
            ["Verifications", "shield"],
            ["Repositories", "branch"],
            ["Check coverage", "grid"],
          ].map(([label, icon]) => (
            <button
              key={label}
              className={page === label ? "nav-item active" : "nav-item"}
              onClick={() => setPage(label)}
            >
              <Icon name={icon} />
              {label}
              {label === "Verifications" && (
                <span className="nav-count">{runs.length + 1}</span>
              )}
            </button>
          ))}
        </nav>
        <div className="sidebar-bottom">
          <div className="local-note">
            <span className="tiny-dot" />
            Local preview
            <small>
              Your verification workspace.
              <br />
              Independent by design.
            </small>
          </div>
          <a
            href="https://github.com/shuklashreyas/CodeHound"
            target="_blank"
            rel="noreferrer"
          >
            <Icon name="github" size={16} />
            Project on GitHub
            <Icon name="arrow" size={14} />
          </a>
          <div className="user">
            <span className="avatar">D</span>
            <div>
              {auth.session?.user?.login || "Developer"}
              <small>
                {auth.session?.user ? "GitHub connected" : "Personal workspace"}
              </small>
            </div>
            <span className="version">v0.1</span>
          </div>
        </div>
      </aside>
      <div className="main-shell" inert={modal}>
        <header className="topbar">
          <div>
            Workspace <span>/</span> <strong>{page}</strong>
          </div>
          <GitHubAccount
            auth={auth}
            onConnect={() => setPage("Repositories")}
          />
        </header>
        <main>
          <div className="page-heading">
            <div>
              <div className="eyebrow">INDEPENDENT CODE VERIFICATION</div>
              <h1>{page}</h1>
              <p>
                {page === "Verifications"
                  ? "Follow the evidence. Ship with confidence."
                  : page === "Repositories"
                    ? "A home for the repositories you want to verify."
                    : "Every dimension of a change, in one place."}
              </p>
            </div>
            <button
              className="button primary"
              onClick={() => {
                setModal(true);
                setError("");
              }}
            >
              <Icon name="plus" size={17} />
              New verification
            </button>
          </div>
          {page === "Verifications" && (
            <>
              <div className="notice">
                <Icon name="info" size={17} />
                <span>
                  {run.sample ? (
                    <>
                      <strong>You’re viewing a sample report.</strong> Explore
                      the workflow with illustrative results. No code has been
                      executed.
                    </>
                  ) : (
                    <>
                      <strong>
                        {run.persisted
                          ? "Draft saved to your workspace."
                          : "Draft created in this session."}
                      </strong>{" "}
                      No code has been executed. All verification checks are not
                      run.
                    </>
                  )}
                </span>
                {!run.sample && (
                  <button onClick={() => chooseRun(sample)}>
                    View sample <span>↗</span>
                  </button>
                )}
              </div>
              <div className="run-selector">
                <span>REPORT</span>
                <select
                  aria-label="Select verification report"
                  value={run.id}
                  onChange={(e) =>
                    chooseRun(
                      [sample, ...runs].find((r) => r.id === e.target.value)!,
                    )
                  }
                >
                  <option value={sample.id}>Sample · session expiration</option>
                  {runs.map((r) => (
                    <option key={r.id} value={r.id}>
                      {r.repo} · {r.title}
                    </option>
                  ))}
                </select>
                <span className="session-note">
                  {auth.session?.user
                    ? "Latest 100 saved drafts"
                    : "Drafts last for this session"}
                </span>
              </div>
              <section className="report-card">
                <div className="report-heading">
                  <div>
                    <div className="repo-label">
                      <Icon name="github" size={16} />
                      {run.repo}
                      <span className="pill">
                        {run.sample ? "SAMPLE" : "DRAFT"}
                      </span>
                    </div>
                    <h2>{run.title}</h2>
                    <div className="report-meta">
                      {run.sample ? (
                        <>
                          <span>
                            <Icon name="branch" size={14} />
                            fix/session-expiration
                          </span>
                          <i /> <span>8c4e2a1 → f7b93d2</span>
                          <i />
                          <span>
                            <Icon name="clock" size={14} />
                            1m 24s · sample
                          </span>
                        </>
                      ) : (
                        <>
                          <span>{run.date}</span>
                          <i />
                          <a href={run.pr} target="_blank" rel="noreferrer">
                            Open pull request ↗
                          </a>
                        </>
                      )}
                    </div>
                  </div>
                  <button className="button secondary" onClick={exportReport}>
                    <Icon name="down" size={16} />
                    Export report
                  </button>
                </div>
                <div className={`verdict ${run.sample ? "" : "draft-verdict"}`}>
                  <span className="verdict-icon">
                    <Icon name={run.sample ? "info" : "clock"} size={22} />
                  </span>
                  <div>
                    <strong>
                      {run.sample
                        ? "Changes need attention"
                        : "Ready for backend integration"}
                    </strong>
                    <p>
                      {run.sample
                        ? "Visible tests pass, but independent checks reveal an incomplete fix."
                        : "Your PR and task description are captured. No repository has been fetched or evaluated."}
                    </p>
                  </div>
                  <span className="verdict-label">
                    {run.sample ? "SAMPLE VERDICT" : "NOT RUN"}
                  </span>
                </div>
                <div className="stats">
                  <div>
                    <span>Checks passed</span>
                    <strong>
                      {currentChecks.filter((c) => c.status === "Pass").length}
                      <small> / 13</small>
                    </strong>
                  </div>
                  <div>
                    <span>Needs attention</span>
                    <strong className={run.sample ? "orange-text" : ""}>
                      {run.sample
                        ? currentChecks.filter(
                            (c) =>
                              c.status === "Fail" || c.status === "Partial",
                          ).length
                        : "—"}
                      <small>{run.sample ? " checks" : ""}</small>
                    </strong>
                  </div>
                  <div>
                    <span>Files changed</span>
                    <strong>
                      {run.sample ? "2" : "—"}
                      {run.sample && (
                        <small className="diff-count">
                          <b>+18</b> −6
                        </small>
                      )}
                    </strong>
                  </div>
                  <div>
                    <span>Confidence</span>
                    <strong className="confidence">Not scored</strong>
                    <small>Requires calibrated evidence</small>
                  </div>
                </div>
                <div
                  className="tabs"
                  role="tablist"
                  aria-label="Report sections"
                >
                  {tabs.map((t) => (
                    <button
                      key={t}
                      id={`tab-${t.replaceAll(" ", "-")}`}
                      role="tab"
                      tabIndex={tab === t ? 0 : -1}
                      onKeyDown={(event) => {
                        const direction =
                          event.key === "ArrowRight"
                            ? 1
                            : event.key === "ArrowLeft"
                              ? -1
                              : 0;
                        if (
                          !direction &&
                          event.key !== "Home" &&
                          event.key !== "End"
                        )
                          return;
                        event.preventDefault();
                        const next =
                          event.key === "Home"
                            ? tabs[0]
                            : event.key === "End"
                              ? tabs[tabs.length - 1]
                              : tabs[
                                  (tabs.indexOf(t) + direction + tabs.length) %
                                    tabs.length
                                ];
                        setTab(next);
                        document
                          .getElementById(`tab-${next.replaceAll(" ", "-")}`)
                          ?.focus();
                      }}
                      aria-selected={tab === t}
                      aria-controls="report-panel"
                      className={tab === t ? "selected" : ""}
                      onClick={() => setTab(t)}
                    >
                      {t}
                      {t === "Changed files" && run.sample && <span>2</span>}
                    </button>
                  ))}
                </div>
              </section>
              <div
                id="report-panel"
                role="tabpanel"
                aria-labelledby={`tab-${tab.replaceAll(" ", "-")}`}
              >
                {tab === "Overview" && (
                  <div className="overview-grid">
                    <section className="panel checks-panel">
                      <div className="panel-heading">
                        <h3>
                          Verification checks <span>13</span>
                        </h3>
                        <select
                          aria-label="Filter checks"
                          value={filter}
                          onChange={(e) => setFilter(e.target.value)}
                        >
                          {[
                            "All checks",
                            "Needs attention",
                            "Passed",
                            "Not run",
                          ].map((f) => (
                            <option key={f}>{f}</option>
                          ))}
                        </select>
                      </div>
                      <div className="table-heading">
                        <span>CHECK</span>
                        <span>RESULT</span>
                      </div>
                      {currentChecks
                        .filter(
                          (c) =>
                            filter === "All checks" ||
                            (filter === "Needs attention"
                              ? ["Fail", "Partial"].includes(c.status)
                              : c.status ===
                                (filter === "Passed" ? "Pass" : "Not run")),
                        )
                        .map((c) => (
                          <div className="check-item" key={c.name}>
                            <button
                              className="check-row"
                              aria-expanded={expanded === c.name}
                              onClick={() =>
                                setExpanded(expanded === c.name ? null : c.name)
                              }
                            >
                              <span className="check-number">
                                {String(
                                  checks.findIndex(
                                    (check) => check.name === c.name,
                                  ) + 1,
                                ).padStart(2, "0")}
                              </span>
                              <span className="check-name">
                                {c.name}
                                <small>{c.description}</small>
                              </span>
                              <Badge status={c.status} />
                              <span
                                className={
                                  expanded === c.name
                                    ? "rotate chevron"
                                    : "chevron"
                                }
                              >
                                <Icon name="chevron" size={14} />
                              </span>
                            </button>
                            {expanded === c.name && (
                              <div className="check-detail">
                                <strong>
                                  {run.sample ? "Sample evidence" : "Evidence"}
                                </strong>
                                <p>{c.evidence}</p>
                              </div>
                            )}
                          </div>
                        ))}
                      {currentChecks.filter(
                        (c) =>
                          filter === "All checks" ||
                          (filter === "Needs attention"
                            ? ["Fail", "Partial"].includes(c.status)
                            : c.status ===
                              (filter === "Passed" ? "Pass" : "Not run")),
                      ).length === 0 && (
                        <div className="empty-state">
                          No checks in this category.
                        </div>
                      )}
                      <div className="panel-foot">
                        <Icon name="info" size={14} />
                        Select a check to inspect its supporting evidence.
                      </div>
                    </section>
                    <div className="right-column">
                      <section className="panel finding-panel">
                        <div className="panel-heading">
                          <h3>Key finding</h3>
                          <span className="orange-dot" />
                        </div>
                        {run.sample ? (
                          <>
                            <span className="severity">
                              REGRESSION · SAMPLE
                            </span>
                            <h3>
                              The session expires.
                              <br />
                              The refresh token shouldn’t.
                            </h3>
                            <p>
                              The patch fixes expiration for normal sessions,
                              but refresh-token validation still relies on the
                              expired session.
                            </p>
                            <div className="file-reference">
                              <span>LIKELY AFFECTED</span>
                              <code>src/middleware/auth.ts</code>
                            </div>
                            <button
                              className="text-button"
                              onClick={() => setTab("Execution evidence")}
                            >
                              Inspect execution evidence
                              <Icon name="arrow" size={15} />
                            </button>
                          </>
                        ) : (
                          <div className="empty-state">
                            <Icon name="shield" size={28} />
                            <h3>No findings yet</h3>
                            <p>
                              Findings will appear after the verification runner
                              evaluates this change.
                            </p>
                          </div>
                        )}
                      </section>
                      <section className="panel">
                        <div className="panel-heading">
                          <h3>Verification trail</h3>
                        </div>
                        <ol className="trail">
                          {[
                            "Repository & task",
                            "Agent patch",
                            "Independent checks",
                            "Execution evidence",
                            "Verification report",
                          ].map((step, index) => (
                            <li key={step}>
                              <span
                                className={
                                  run.sample ? "trail-dot done" : "trail-dot"
                                }
                              >
                                {run.sample ? (
                                  <Icon name="check" size={11} />
                                ) : (
                                  index + 1
                                )}
                              </span>
                              <div>
                                {step}
                                <small>
                                  {run.sample
                                    ? [
                                        "Sample issue & repository",
                                        "2 files · 24 lines changed",
                                        "13 dimensions reviewed",
                                        "Tests, analysis & dependencies",
                                        "Illustrative results",
                                      ][index]
                                    : [
                                        "Draft inputs captured",
                                        "Not fetched",
                                        "Not run",
                                        "Not available",
                                        "Awaiting evaluation",
                                      ][index]}
                                </small>
                              </div>
                            </li>
                          ))}
                        </ol>
                      </section>
                      <div className="trust-note">
                        <Icon name="shield" size={17} />
                        <p>
                          Evidence over assumptions.
                          <br />
                          <span>
                            Unverified behavior is never marked as passed.
                          </span>
                        </p>
                      </div>
                    </div>
                  </div>
                )}
                {tab === "Changed files" && (
                  <section className="panel">
                    <div className="panel-heading">
                      <h3>Patch scope</h3>
                      <span className="muted">
                        {run.sample
                          ? "Illustrative diff summary"
                          : "Awaiting repository intake"}
                      </span>
                    </div>
                    {run.sample ? (
                      <>
                        {[
                          ["src/services/session.ts", "+14", "−4"],
                          ["src/utils/token.ts", "+4", "−2"],
                        ].map(([file, add, remove]) => (
                          <div className="file-row" key={file}>
                            <Icon name="file" />
                            <code>{file}</code>
                            <span className="green-text">{add}</span>
                            <span className="orange-text">{remove}</span>
                          </div>
                        ))}
                        <div className="impact-box">
                          <h3>Downstream dependency</h3>
                          <div className="dependency">
                            <code>session.ts</code>
                            <Icon name="arrow" />
                            <code>middleware/auth.ts</code>
                            <Icon name="arrow" />
                            <code>routes/refresh.ts</code>
                          </div>
                          <p>
                            The unchanged authentication middleware depends on
                            the modified session validation behavior. This
                            sample identifies the refresh flow as affected.
                          </p>
                        </div>
                      </>
                    ) : (
                      <div className="empty-state">
                        Changed files will appear once CodeHound fetches the PR.
                      </div>
                    )}
                  </section>
                )}
                {tab === "Execution evidence" && (
                  <section className="panel">
                    <div className="panel-heading">
                      <h3>Execution evidence</h3>
                      <span className="pill">
                        {run.sample ? "SAMPLE OUTPUT" : "NOT RUN"}
                      </span>
                    </div>
                    {run.sample ? (
                      <>
                        <div className="evidence-summary">
                          <div>
                            <span>Original visible suite</span>
                            <strong>42 / 42 passed</strong>
                          </div>
                          <div>
                            <span>Candidate visible suite</span>
                            <strong>42 / 42 passed</strong>
                          </div>
                          <div>
                            <span>Independent suite</span>
                            <strong className="orange-text">
                              7 / 8 passed
                            </strong>
                          </div>
                        </div>
                        <div className="log-heading">
                          <span className="orange-dot" />
                          test_refresh_after_expiration
                        </div>
                        <pre className="log">{`ILLUSTRATIVE OUTPUT — not an actual execution\n\nBaseline    PASS  refresh token valid after session expiration\nCandidate   FAIL  refresh token valid after session expiration\n\n  POST /auth/refresh\n  session.expired = true\n  refreshToken.expired = false\n\n  Expected: 200 OK\n  Received: 401 Unauthorized\n\n  at tests/hidden/refresh.test.ts:38\n  via src/middleware/auth.ts:24\n\nStatic analysis: no new findings in sample comparison\nTest integrity: original test files unchanged`}</pre>
                      </>
                    ) : (
                      <div className="empty-state">
                        No commands have been executed. Logs and test results
                        will appear here after a run.
                      </div>
                    )}
                  </section>
                )}
                {tab === "Requirements" && (
                  <section className="panel">
                    <div className="panel-heading">
                      <h3>Issue & acceptance criteria</h3>
                    </div>
                    <div className="issue-text">
                      <span className="eyebrow">TASK DESCRIPTION</span>
                      <p>{run.issue || "No task description supplied."}</p>
                    </div>
                    {run.sample ? (
                      [
                        [
                          "Expired sessions are rejected on protected routes.",
                          "Pass",
                        ],
                        [
                          "Valid refresh tokens work after session expiration.",
                          "Fail",
                        ],
                        ["Expired refresh tokens are rejected.", "Pass"],
                      ].map(([text, status]) => (
                        <div className="requirement" key={text}>
                          <span>{text}</span>
                          <Badge status={status as Status} />
                        </div>
                      ))
                    ) : (
                      <div className="empty-state">
                        Acceptance criteria have not been analyzed.
                      </div>
                    )}
                  </section>
                )}
              </div>
            </>
          )}
          {page === "Repositories" && (
            <>
              <GitHubRepositories
                auth={auth}
                onSelect={(pull) => {
                  setUrl(pull.url);
                  setIssue(pull.body || pull.title);
                  setError("");
                  setModal(true);
                }}
              />
              <section className="panel">
                <div className="panel-heading">
                  <h3>Repositories in this session</h3>
                  <input
                    className="search"
                    aria-label="Search repositories"
                    placeholder="Search repositories…"
                    value={query}
                    onChange={(e) => setQuery(e.target.value)}
                  />
                </div>
                <div className="notice inline-notice">
                  <Icon name="info" />
                  <span>
                    Only public GitHub repositories are planned for V1. Draft
                    URLs have not been checked for visibility or existence.
                  </span>
                </div>
                {[
                  sample,
                  ...runs.filter(
                    (r, i, list) =>
                      list.findIndex((x) => x.repo === r.repo) === i,
                  ),
                ]
                  .filter((r) =>
                    r.repo.toLowerCase().includes(query.toLowerCase()),
                  )
                  .map((r) => (
                    <button
                      className="repository-row"
                      key={r.id}
                      onClick={() => chooseRun(r)}
                    >
                      <span className="repo-icon">
                        <Icon name="github" size={22} />
                      </span>
                      <span>
                        <strong>{r.repo}</strong>
                        <small>
                          {r.sample
                            ? "Illustrative repository"
                            : "Draft · not fetched"}
                        </small>
                      </span>
                      <span className="pill">
                        {r.sample ? "SAMPLE" : "DRAFT"}
                      </span>
                      <Icon name="arrow" />
                    </button>
                  ))}
                {![sample, ...runs].some((r) =>
                  r.repo.toLowerCase().includes(query.toLowerCase()),
                ) && (
                  <div className="empty-state">
                    No repositories match your search.
                  </div>
                )}
              </section>
            </>
          )}
          {page === "Check coverage" && (
            <>
              <div className="notice">
                <Icon name="info" />
                <span>
                  <strong>The verification roadmap.</strong> These checks
                  describe the intended evaluator. None are connected to a live
                  runner yet.
                </span>
              </div>
              <div className="coverage-grid">
                {checks.map((c, i) => (
                  <section className="panel coverage-card" key={c.name}>
                    <div>
                      <span className="coverage-number">
                        {String(i + 1).padStart(2, "0")}
                      </span>
                      <Badge status="Not run" />
                    </div>
                    <h3>{c.name}</h3>
                    <p>{c.description}</p>
                  </section>
                ))}
              </div>
            </>
          )}
          <footer>
            <span>
              CodeHound <span className="footer-separator">/</span> Verify what
              coding agents actually ship.
            </span>
            <span>Independent verification · v0.1</span>
          </footer>
        </main>
      </div>
      {toast && (
        <div className="toast" role="status">
          <Icon name="check" />
          {toast}
          <button
            aria-label="Dismiss notification"
            onClick={() => setToast("")}
          >
            <Icon name="close" size={14} />
          </button>
        </div>
      )}
      {modal && (
        <NewVerification
          onClose={() => {
            if (!saving) setModal(false);
          }}
        >
          <form onSubmit={submit}>
            <div className="modal-heading">
              <span className="modal-icon">
                <Icon name="branch" size={24} />
              </span>
              <button
                type="button"
                disabled={saving}
                className="icon-button"
                aria-label="Close dialog"
                onClick={() => setModal(false)}
              >
                <Icon name="close" />
              </button>
            </div>
            <h2 id="new-title">Follow the change.</h2>
            <p className="muted">
              Start with a public GitHub pull request and the task it should
              solve.
            </p>
            <label htmlFor="pr-url">
              Pull request URL <span>Required</span>
            </label>
            <input
              autoFocus
              id="pr-url"
              type="url"
              required
              placeholder="https://github.com/owner/repo/pull/123"
              value={url}
              onChange={(e) => {
                setUrl(e.target.value);
                setError("");
              }}
              aria-describedby={error ? "form-error" : undefined}
            />
            <label htmlFor="issue">
              Issue or task description <span>Required</span>
            </label>
            <textarea
              id="issue"
              required
              minLength={10}
              maxLength={20000}
              rows={4}
              placeholder="Describe the expected behavior or paste the issue details…"
              value={issue}
              onChange={(e) => setIssue(e.target.value)}
            />
            <p className="form-hint">
              Include acceptance criteria and edge cases. The PR supplies the
              repository and commit references once intake is connected.
            </p>
            {error && (
              <p id="form-error" role="alert" className="form-error">
                {error}
              </p>
            )}
            <div className="draft-note">
              <Icon name="info" size={17} />
              <span>
                {auth.session?.user
                  ? "This draft is saved to your account and survives page refreshes. Verification is not run automatically."
                  : "Sign in to save drafts to your account. Without sign-in, this draft lasts only for this session."}
              </span>
            </div>
            <div className="modal-actions">
              <button
                className="button secondary"
                type="button"
                disabled={saving}
                onClick={() => setModal(false)}
              >
                Cancel
              </button>
              <button
                className="button primary"
                type="submit"
                disabled={saving}
              >
                {saving ? "Saving…" : "Create draft"}
                <Icon name="arrow" size={17} />
              </button>
            </div>
          </form>
        </NewVerification>
      )}
    </div>
  );
}
function NewVerification({
  children,
  onClose,
}: {
  children: ReactNode;
  onClose: () => void;
}) {
  useEffect(() => {
    const originalOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.body.style.overflow = originalOverflow;
      document
        .querySelector<HTMLButtonElement>(".page-heading button")
        ?.focus();
    };
  }, []);
  return (
    <div
      className="modal-backdrop"
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
      onKeyDown={(e) => {
        if (e.key === "Escape") onClose();
        if (e.key === "Tab") {
          const items = e.currentTarget.querySelectorAll<HTMLElement>(
            "button, input, textarea",
          );
          const first = items[0];
          const last = items[items.length - 1];
          if (e.shiftKey && document.activeElement === first) {
            e.preventDefault();
            last.focus();
          } else if (!e.shiftKey && document.activeElement === last) {
            e.preventDefault();
            first.focus();
          }
        }
      }}
    >
      <section
        className="modal"
        role="dialog"
        aria-modal="true"
        aria-labelledby="new-title"
      >
        {children}
      </section>
    </div>
  );
}
createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
