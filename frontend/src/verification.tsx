import { useEffect, useRef, useState } from "react";
import { api, ApiError } from "./github";
import { ImpactEvidence } from "./impact";
import type { PythonImpact } from "./impact";
import { Requirements } from "./requirements";
import type { RequirementEvidence } from "./requirements";
import { IntegrityEvidence } from "./integrity";
import type { IntegrityResult } from "./integrity";

type Profile = {
  id: string;
  label: string;
  coverage: string;
  visible_cases: number;
  hidden_cases: number;
};
type Failure = { code: string; message: string };
type Transition = { nodeid: string; before: string; after: string };
type Comparison = {
  verdict: string;
  reasons: string[];
  counts: Record<string, number>;
  improvements: Transition[];
  regressions: Transition[];
  unresolved: Transition[];
  unchanged_passes: Transition[];
  unverified: Transition[];
  missing_tests: string[];
  added_tests: string[];
};
type Suite = {
  baseline: unknown;
  candidate: unknown;
  test_comparison: Comparison;
};
type Job = {
  id: string;
  status: string;
  stage: string;
  cancel_requested: boolean;
  profile: Profile;
  base_sha: string;
  head_sha: string;
  image_id: string;
  diff_sha256: string;
  assessment: { verdict: string; signals: string[] } | null;
  failure: Failure | null;
  created_at: string;
  artifact?: {
    suites: Record<string, Suite>;
    limitations: string[];
    test_integrity?: IntegrityResult | null;
    requirement_evidence?: RequirementEvidence;
    python_impact?: PythonImpact | null;
  } | null;
};
type Report = {
  id: string;
  title: string;
  repository: string;
  pr_url: string;
  issue_text: string;
  status: string;
  failure: Failure | null;
  checks: { name: string; status: string; explanation: string }[];
  snapshot: {
    base_sha: string;
    head_sha: string;
    diff_sha256: string;
    diff: string;
    captured_at: string;
    files: {
      filename: string;
      status: string;
      additions: number;
      deletions: number;
    }[];
    observations: {
      kind: string;
      path: string;
      explanation?: string;
      message?: string;
    }[];
  } | null;
};
type Availability = {
  configured: boolean;
  worker_online: boolean;
  profiles: Profile[];
};
const active = (job: Job) => ["queued", "running"].includes(job.status);
const words = (value: string) => value.replaceAll("_", " ");
const verdicts: Record<string, string> = {
  regression_detected: "Regression detected",
  incomplete: "Independent checks still fail",
  candidate_improves: "Improvement observed",
  no_behavior_change_observed: "No behavior change observed",
  inconclusive: "Evidence is inconclusive",
};
const stages: Record<string, string> = {
  queued: "Waiting for a worker",
  checkout: "Checking out both revisions",
  test_integrity: "Inspecting changes to test structure",
  repository_impact: "Inspecting Python imports and syntax",
  visible_baseline: "Running visible checks · baseline",
  visible_candidate: "Running visible checks · candidate",
  hidden_baseline: "Running independent checks · baseline",
  hidden_candidate: "Running independent checks · candidate",
};
function saveJson(value: unknown, name: string) {
  const url = URL.createObjectURL(
    new Blob([JSON.stringify(value, null, 2)], { type: "application/json" }),
  );
  const link = document.createElement("a");
  link.href = url;
  link.download = name;
  link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
function ResultBadge({ value }: { value: string }) {
  const color =
    value === "pass"
      ? "pass"
      : value === "fail"
        ? "fail"
        : value === "needs_review"
          ? "partial"
          : "not-run";
  return <span className={`badge ${color}`}>{words(value)}</span>;
}

export function Verification({
  id,
  onUnauthorized,
}: {
  id: string;
  onUnauthorized: () => void;
}) {
  const [report, setReport] = useState<Report | null>(null);
  const [availability, setAvailability] = useState<Availability | null>(null);
  const [jobs, setJobs] = useState<Job[]>([]);
  const [job, setJob] = useState<Job | null>(null);
  const [selectedJob, setSelectedJob] = useState("");
  const [profileId, setProfileId] = useState("");
  const [revision, setRevision] = useState(0);
  const [error, setError] = useState("");
  const [loadError, setLoadError] = useState("");
  const [retryAttempt, setRetryAttempt] = useState(0);
  const [busy, setBusy] = useState("");
  const pending = useRef(false);
  const submission = useRef<{ profile: string; key: string } | null>(null);
  const lifetime = useRef<AbortController | null>(null);
  useEffect(() => {
    const controller = new AbortController();
    lifetime.current = controller;
    return () => controller.abort();
  }, []);
  useEffect(() => {
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout>;
    let failures = 0;
    async function load() {
      try {
        const [next, profiles, history] = await Promise.all([
          api<Report>(`/verifications/${id}`, { signal: controller.signal }),
          api<Availability>(`/verifications/${id}/profiles`, {
            signal: controller.signal,
          }),
          api<Job[]>(`/verifications/${id}/executions`, {
            signal: controller.signal,
          }),
        ]);
        if (controller.signal.aborted) return;
        const chosen = selectedJob || history[0]?.id;
        const detail = chosen
          ? await api<Job>(`/executions/${chosen}`, {
              signal: controller.signal,
            })
          : null;
        if (controller.signal.aborted) return;
        failures = 0;
        setLoadError("");
        setRetryAttempt(0);
        setReport(next);
        setAvailability(profiles);
        setJobs(history);
        setJob(detail);
        setProfileId((previous) =>
          profiles.profiles.some((p) => p.id === previous)
            ? previous
            : profiles.profiles[0]?.id || "",
        );
        if (next.status === "intaking" || history.some(active))
          timer = setTimeout(() => void load(), 2000);
      } catch (failure) {
        if (controller.signal.aborted) return;
        setLoadError((failure as Error).message);
        const temporary =
          failure instanceof ApiError &&
          (failure.status === 0 ||
            failure.status === 429 ||
            failure.status >= 500);
        failures += 1;
        if (temporary && failures <= 4) {
          setRetryAttempt(failures);
          timer = setTimeout(() => void load(), 2000 * 2 ** (failures - 1));
        } else {
          setRetryAttempt(0);
        }
        if (failure instanceof ApiError && failure.status === 401)
          onUnauthorized();
      }
    }
    void load();
    return () => {
      controller.abort();
      clearTimeout(timer);
    };
  }, [id, revision, selectedJob, onUnauthorized]);

  async function action(
    label: string,
    path: string,
    body?: unknown,
    key?: string,
    exportName?: string,
  ) {
    if (pending.current) return;
    pending.current = true;
    setBusy(label);
    setError("");
    const signal = lifetime.current!.signal;
    try {
      const value = await api(
        path,
        {
          method: exportName ? "GET" : "POST",
          signal,
          headers: {
            "Content-Type": "application/json",
            "X-CodeHound-Request": "1",
            ...(key ? { "Idempotency-Key": key } : {}),
          },
          ...(body ? { body: JSON.stringify(body) } : {}),
        },
        label === "capture" ? 110_000 : 20_000,
      );
      if (signal.aborted) return;
      if (exportName) saveJson(value, exportName);
      if (label === "run") {
        submission.current = null;
        setSelectedJob("");
        setJob(null);
      }
    } catch (failure) {
      if (signal.aborted) return;
      setError((failure as Error).message);
      if (failure instanceof ApiError && failure.status === 401)
        onUnauthorized();
    } finally {
      pending.current = false;
      if (!signal.aborted) {
        setBusy("");
        setRevision((value) => value + 1);
      }
    }
  }
  function run() {
    if (!submission.current || submission.current.profile !== profileId)
      submission.current = { profile: profileId, key: crypto.randomUUID() };
    void action(
      "run",
      `/verifications/${id}/executions`,
      { profile_id: profileId },
      submission.current.key,
    );
  }
  const profile = availability?.profiles.find((p) => p.id === profileId);
  const running = jobs.find(active);
  return (
    <div className="live-verification">
      {(error || loadError) && (
        <div className="github-error" role="alert">
          {error || loadError}
          {loadError && retryAttempt > 0 && (
            <span> Retrying evidence load ({retryAttempt}/4)…</span>
          )}
          <button
            onClick={() => {
              setError("");
              setLoadError("");
              setRetryAttempt(0);
              setRevision((r) => r + 1);
            }}
          >
            Refresh evidence
          </button>
        </div>
      )}
      {!report ? (
        <section className="panel empty-state" role="status">
          {loadError
            ? retryAttempt
              ? "Waiting to retry evidence loading…"
              : "Evidence unavailable. Refresh to try again."
            : "Loading saved verification…"}
        </section>
      ) : (
        <>
          <section className="report-card">
            <div className="report-heading">
              <div>
                <div className="repo-label">
                  {report.repository}
                  <span className="pill">SAVED</span>
                </div>
                <h2>{report.title}</h2>
                <a href={report.pr_url} target="_blank" rel="noreferrer">
                  Open pull request ↗
                </a>
              </div>
              <button
                className="button secondary"
                disabled={!!busy}
                onClick={() =>
                  void action(
                    "export",
                    `/verifications/${id}/export`,
                    undefined,
                    undefined,
                    `codehound-${id}.json`,
                  )
                }
              >
                Export report
              </button>
            </div>
            <div className="execution-steps" aria-label="Verification workflow">
              <span className={report.snapshot ? "done" : "current"}>
                01 · Capture PR
              </span>
              <span
                className={
                  running
                    ? "current"
                    : job?.status === "completed"
                      ? "done"
                      : ""
                }
              >
                02 · Execute both revisions
              </span>
              <span className={job?.status === "completed" ? "current" : ""}>
                03 · Compare evidence
              </span>
            </div>
            <div className="execution-controls">
              <div>
                <h3>
                  {report.snapshot
                    ? "PR snapshot captured"
                    : "Capture the change"}
                </h3>
                <p className="muted">
                  {report.snapshot
                    ? "Both revisions are pinned. New PR commits require a new verification."
                    : "Save the exact baseline, candidate and diff from GitHub before running checks."}
                </p>
              </div>
              <button
                className="button secondary"
                disabled={
                  !!busy || report.status === "intaking" || !!report.snapshot
                }
                onClick={() =>
                  void action("capture", `/verifications/${id}/intake`)
                }
              >
                {busy === "capture" || report.status === "intaking"
                  ? "Capturing PR…"
                  : report.snapshot
                    ? "Captured"
                    : "Capture PR"}
              </button>
            </div>
            {report.failure && (
              <p className="execution-warning" role="alert">
                {report.failure.message}
              </p>
            )}
            {report.snapshot && (
              <div className="revision-pair">
                <span>
                  BASELINE{" "}
                  <code title={report.snapshot.base_sha}>
                    {report.snapshot.base_sha.slice(0, 12)}
                  </code>
                </span>
                <span aria-hidden="true">→</span>
                <span>
                  CANDIDATE{" "}
                  <code title={report.snapshot.head_sha}>
                    {report.snapshot.head_sha.slice(0, 12)}
                  </code>
                </span>
              </div>
            )}
            <div className="execution-controls profile-controls">
              <div>
                <h3>Independent test profile</h3>
                {availability?.profiles.length ? (
                  <>
                    <select
                      aria-label="Independent test profile"
                      value={profileId}
                      disabled={!!busy || !!running}
                      onChange={(e) => {
                        setProfileId(e.target.value);
                        submission.current = null;
                      }}
                    >
                      {availability.profiles.map((p) => (
                        <option key={p.id} value={p.id}>
                          {p.label}
                        </option>
                      ))}
                    </select>
                    <p className="profile-coverage">{profile?.coverage}</p>
                    <small>
                      {profile?.visible_cases} visible · {profile?.hidden_cases}{" "}
                      independent cases
                    </small>
                  </>
                ) : (
                  <p className="muted">
                    No test profile is configured for this repository. An
                    operator must supply independent checks before it can run.
                  </p>
                )}
                {availability && !availability.configured && (
                  <p className="execution-warning">
                    Execution image is not configured on the backend.
                  </p>
                )}
                {availability && !availability.worker_online && (
                  <p className="muted">
                    Worker offline. Start the backend worker, then refresh its
                    status.
                  </p>
                )}
              </div>
              <div className="execution-actions">
                <button
                  className="button primary"
                  disabled={
                    !!busy ||
                    !!running ||
                    report.status !== "ready" ||
                    !profile ||
                    !availability?.configured ||
                    !availability.worker_online
                  }
                  onClick={run}
                >
                  {busy === "run" ? "Queuing…" : "Run verification"}
                </button>
                <button
                  className="button secondary"
                  disabled={!!busy}
                  onClick={() => {
                    setError("");
                    setRevision((r) => r + 1);
                  }}
                >
                  Refresh status
                </button>
              </div>
            </div>
            {running && (
              <div className="execution-progress" role="status">
                <span>
                  <strong>
                    {stages[running.stage] || words(running.stage)}
                  </strong>
                  <small>
                    {running.cancel_requested
                      ? "Cancellation requested. Waiting for cleanup."
                      : "This page updates as the worker saves progress."}
                  </small>
                </span>
                <button
                  className="button secondary"
                  disabled={!!busy || running.cancel_requested}
                  onClick={() =>
                    void action("cancel", `/executions/${running.id}/cancel`)
                  }
                >
                  Cancel execution
                </button>
              </div>
            )}
          </section>
          {jobs.length > 0 && (
            <section className="panel execution-evidence">
              <div className="panel-heading">
                <h3>Execution evidence</h3>
                <select
                  aria-label="Execution history"
                  value={selectedJob || jobs[0].id}
                  onChange={(e) => {
                    setSelectedJob(e.target.value);
                    setJob(null);
                  }}
                >
                  {jobs.map((item) => (
                    <option value={item.id} key={item.id}>
                      {new Date(item.created_at).toLocaleString()} ·{" "}
                      {words(item.status)} · {item.id.slice(0, 8)}
                    </option>
                  ))}
                </select>
              </div>
              {!job ? (
                <p className="evidence-body" role="status">
                  Loading execution evidence…
                </p>
              ) : (
                <>
                  <div className="evidence-body">
                    <div className="execution-verdict">
                      <h2>
                        {job.assessment
                          ? verdicts[job.assessment.verdict] ||
                            words(job.assessment.verdict)
                          : words(job.status)}
                      </h2>
                      <button
                        className="button secondary"
                        disabled={!!busy}
                        onClick={() =>
                          void action(
                            "export",
                            `/executions/${job.id}/export`,
                            undefined,
                            undefined,
                            `codehound-execution-${job.id}.json`,
                          )
                        }
                      >
                        Export execution
                      </button>
                    </div>
                    <p className="profile-coverage">{job.profile.coverage}</p>
                    <p className="muted">
                      Confidence: not scored. Full requirement adherence remains
                      unverified.
                    </p>
                    {job.failure && (
                      <p className="execution-warning" role="alert">
                        {job.failure.message}
                      </p>
                    )}
                    {job.status === "cancelled" && (
                      <p>
                        This execution was cancelled. No verdict was issued.
                      </p>
                    )}
                    {job.assessment?.signals.map((signal) => (
                      <p className="execution-warning" key={signal}>
                        {words(signal)}
                      </p>
                    ))}
                    <details className="evidence-details">
                      <summary>Execution identity</summary>
                      <dl>
                        {Object.entries({
                          "Baseline SHA": job.base_sha,
                          "Candidate SHA": job.head_sha,
                          "Diff SHA-256": job.diff_sha256,
                          "Image ID": job.image_id,
                        }).map(([name, value]) => (
                          <div key={name}>
                            <dt>{name}</dt>
                            <dd>
                              <code>{value}</code>
                            </dd>
                          </div>
                        ))}
                      </dl>
                    </details>
                  </div>
                  {job.artifact && (
                    <>
                      {Object.entries(job.artifact.suites).map(
                        ([name, suite]) => (
                          <SuiteEvidence key={name} name={name} suite={suite} />
                        ),
                      )}
                      {job.artifact.python_impact && (
                        <ImpactEvidence result={job.artifact.python_impact} />
                      )}
                      {job.artifact.requirement_evidence && (
                        <Requirements
                          result={job.artifact.requirement_evidence}
                        />
                      )}
                      {job.artifact.test_integrity && (
                        <IntegrityEvidence
                          result={job.artifact.test_integrity}
                        />
                      )}
                      <ul className="evidence-limitations">
                        {job.artifact.limitations.map((item) => (
                          <li key={item}>{item}</li>
                        ))}
                      </ul>
                    </>
                  )}
                </>
              )}
            </section>
          )}
          <section className="panel">
            <div className="panel-heading">
              <h3>
                Verification checks <span>{report.checks.length}</span>
              </h3>
              <small>Latest execution only</small>
            </div>
            {report.checks.map((check) => (
              <details className="live-check" key={check.name}>
                <summary>
                  <span>{words(check.name)}</span>
                  <ResultBadge value={check.status} />
                </summary>
                <p>{check.explanation}</p>
              </details>
            ))}
          </section>
          <section className="panel evidence-body">
            <h3>Task description</h3>
            <p className="task-description">{report.issue_text}</p>
            <p className="muted">
              Independent checks cover only the selected profile. This
              description has not been semantically verified.
            </p>
          </section>
          {report.snapshot && (
            <section className="panel">
              <div className="panel-heading">
                <h3>
                  Captured change{" "}
                  <span>{report.snapshot.files.length} files</span>
                </h3>
                <small>
                  {new Date(report.snapshot.captured_at).toLocaleString()}
                </small>
              </div>
              <div className="evidence-body">
                {report.snapshot.files.map((file) => (
                  <div className="live-file" key={file.filename}>
                    <code>{file.filename}</code>
                    <span>
                      {file.status} · <b>+{file.additions}</b> −{file.deletions}
                    </span>
                  </div>
                ))}
                {report.snapshot.observations.length > 0 && (
                  <details className="evidence-details">
                    <summary>
                      Integrity review hints (
                      {report.snapshot.observations.length})
                    </summary>
                    <p className="muted">
                      Filename-based observations require review; they are not
                      proof of manipulation.
                    </p>
                    <pre>
                      {JSON.stringify(report.snapshot.observations, null, 2)}
                    </pre>
                  </details>
                )}
                <details className="evidence-details">
                  <summary>Captured diff</summary>
                  <pre>{report.snapshot.diff || "No text diff available."}</pre>
                </details>
              </div>
            </section>
          )}
        </>
      )}
    </div>
  );
}

function SuiteEvidence({ name, suite }: { name: string; suite: Suite }) {
  const comparison = suite.test_comparison;
  const rows = [
    ...comparison.regressions,
    ...comparison.improvements,
    ...comparison.unresolved,
    ...comparison.unverified,
    ...comparison.unchanged_passes,
  ];
  return (
    <section className="suite-evidence">
      <h3>{name === "hidden" ? "Independent checks" : "Visible checks"}</h3>
      <p>{verdicts[comparison.verdict] || words(comparison.verdict)}</p>
      <div className="comparison-counts">
        {[
          "improvements",
          "regressions",
          "unresolved",
          "unchanged_passes",
          "unverified",
        ].map((category) => (
          <span key={category}>
            <strong>{comparison.counts[category] || 0}</strong>
            {words(category)}
          </span>
        ))}
      </div>
      {comparison.reasons.map((reason) => (
        <p className="execution-warning" key={reason}>
          {reason}
        </p>
      ))}
      {rows.length > 0 && (
        <div className="case-table-wrap">
          <table className="case-table">
            <thead>
              <tr>
                <th>Check</th>
                <th>Baseline</th>
                <th>Candidate</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => (
                <tr key={row.nodeid}>
                  <td>
                    <code>{row.nodeid}</code>
                  </td>
                  <td>{words(row.before)}</td>
                  <td className={row.after === "failed" ? "case-failed" : ""}>
                    {words(row.after)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {["missing_tests", "added_tests"].map((category) => {
        const values = comparison[category as "missing_tests" | "added_tests"];
        return values.length ? (
          <p className="execution-warning" key={category}>
            {words(category)}: {values.join(", ")}
          </p>
        ) : null;
      })}
      <details className="evidence-details">
        <summary>Raw execution evidence & logs</summary>
        <p className="muted">
          Candidate output is untrusted. Assertions were evaluated by the
          external controller.
        </p>
        <pre>
          {JSON.stringify(
            { baseline: suite.baseline, candidate: suite.candidate },
            null,
            2,
          )}
        </pre>
      </details>
    </section>
  );
}
