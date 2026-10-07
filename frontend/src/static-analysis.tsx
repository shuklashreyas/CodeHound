export type StaticFinding = {
  path: string;
  line: number;
  column: number;
  end_line: number;
  end_column: number;
  rule: string;
  message: string;
  snippet: string;
  source_sha256: string;
  baseline_line?: number;
  baseline_path?: string;
};

type StaticRevision = {
  status: string;
  files: { path: string; lines: number; bytes: number }[];
  findings: StaticFinding[];
  skipped_files: { path: string; reason: string }[];
  errors: string[];
  source_bytes: number;
  execution?: {
    status: string;
    exit_code: number | null;
    duration_seconds: number;
    output_truncated: boolean;
    oom_killed: boolean;
    timeout_seconds: number;
    network: string;
    memory_mb: number;
    cpu_limit: number;
  };
};

export type StaticAnalysis = {
  status: string;
  image_id: string;
  evaluator_sha256: string;
  tool: { name: string; version: string; rules: string[]; config_sha256: string };
  baseline: StaticRevision;
  candidate: StaticRevision;
  new: StaticFinding[];
  resolved: StaticFinding[];
  existing: StaticFinding[];
  counts: { new: number; resolved: number; existing: number };
  coverage: {
    baseline_files: number;
    candidate_files: number;
    excluded_directories: string[];
    comparison_complete: boolean;
  };
  limits: Record<string, number>;
  limitations: string[];
};

function Findings({ items }: { items: StaticFinding[] }) {
  return items.length ? (
    <ul>
      {items.map((item, index) => (
        <li key={`${item.path}:${item.line}:${item.rule}:${index}`}>
          <code>{item.path}:{item.line}:{item.column}</code>{" "}
          <strong>{item.rule}</strong> · {item.message}
          {item.baseline_line !== undefined && (
            <span className="muted"> · baseline {item.baseline_path}:{item.baseline_line}</span>
          )}
          {item.snippet && <pre>{item.snippet}</pre>}
        </li>
      ))}
    </ul>
  ) : <p>No findings in this category.</p>;
}

export function StaticAnalysisEvidence({ result }: { result: StaticAnalysis }) {
  const complete = result.status === "completed" && result.coverage.comparison_complete;
  return (
    <section className="suite-evidence">
      <h3>Differential Python static analysis</h3>
      <p className="profile-coverage">
        Ruff {result.tool.version} · {result.tool.rules.join(" / ")} rules ·{" "}
        {result.coverage.baseline_files} baseline and {result.coverage.candidate_files} candidate files.
        Static findings identify source issues for review; runtime behavior remains unverified.
      </p>
      {complete ? (
        <>
          <div className="comparison-counts">
            {(["new", "resolved", "existing"] as const).map((kind) => (
              <span key={kind}><strong>{result.counts[kind]}</strong>{kind} findings</span>
            ))}
          </div>
          {result.counts.new === 0 && (
            <p>No new findings in the completed E/F comparison. This does not establish patch correctness.</p>
          )}
          {(["new", "resolved", "existing"] as const).map((kind) => (
            <details className="evidence-details" key={kind} open={kind === "new" && result.new.length > 0}>
              <summary>{kind[0].toUpperCase() + kind.slice(1)} findings ({result.counts[kind]})</summary>
              <Findings items={result[kind]} />
            </details>
          ))}
        </>
      ) : (
        <p className="execution-warning" role="status">
          Static comparison inconclusive. Incomplete evidence cannot establish whether findings are new or resolved.
        </p>
      )}
      {(["baseline", "candidate"] as const).map((name) => {
        const revision = result[name];
        return (
          <details className="evidence-details" key={name}>
            <summary>{name === "baseline" ? "Baseline" : "Candidate"} inspection · {revision.status}</summary>
            {revision.errors.map((error, index) => <p className="execution-warning" key={index}>{error}</p>)}
            {revision.skipped_files.length > 0 && (
              <ul>{revision.skipped_files.map((item, index) => (
                <li key={index}>{item.reason.replaceAll("_", " ")}{item.path && <> · <code>{item.path}</code></>}</li>
              ))}</ul>
            )}
            {!complete && <Findings items={revision.findings} />}
            {revision.execution && <pre>{JSON.stringify(revision.execution, null, 2)}</pre>}
          </details>
        );
      })}
      <details className="evidence-details">
        <summary>Static analysis identity, coverage & limits</summary>
        <ul>{result.limitations.map((item) => <li key={item}>{item}</li>)}</ul>
        <pre>{JSON.stringify({ image_id: result.image_id, tool: result.tool,
          evaluator_sha256: result.evaluator_sha256, limits: result.limits }, null, 2)}</pre>
      </details>
    </section>
  );
}
