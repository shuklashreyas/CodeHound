export type IntegrityResult = {
  status: string;
  files_examined: number;
  findings: {
    kind: string;
    scope: string;
    baseline_path: string;
    candidate_path: string;
    baseline_line: number | null;
    candidate_line: number | null;
    message: string;
  }[];
  unverified: {
    revision: string;
    reason: string;
    baseline_path?: string;
    candidate_path?: string;
  }[];
  limitations: string[];
};

export function IntegrityEvidence({ result }: { result: IntegrityResult }) {
  return (
    <section className="suite-evidence">
      <h3>Structural test review</h3>
      <p className="muted">
        {result.files_examined} changed Python test files selected ·{" "}
        {result.findings.length} review findings
      </p>
      {result.status === "not_applicable" ? (
        <p>
          No changed Python test paths were selected. Other languages and custom
          test layouts remain unverified.
        </p>
      ) : (
        <>
          <p className="profile-coverage">
            These findings identify source changes for review. They do not
            establish that tests were weakened intentionally.
          </p>
          {result.findings.map((finding, index) => (
            <details
              className="evidence-details"
              key={`${finding.kind}:${index}`}
            >
              <summary>
                {finding.kind.replaceAll("_", " ")} ·{" "}
                {finding.scope || "module"}
              </summary>
              <p>{finding.message}</p>
              <dl>
                <dt>Baseline location</dt>
                <dd>
                  <code>
                    {finding.baseline_path}
                    {finding.baseline_line ? `:${finding.baseline_line}` : ""}
                  </code>
                </dd>
                <dt>Candidate location</dt>
                <dd>
                  <code>
                    {finding.candidate_path}
                    {finding.candidate_line
                      ? `:${finding.candidate_line}`
                      : " (location absent)"}
                  </code>
                </dd>
              </dl>
            </details>
          ))}
          {result.status === "completed" && !result.findings.length && (
            <p>
              No structural findings in the inspected files. Assertion strength
              remains unverified.
            </p>
          )}
          {result.unverified.length > 0 && (
            <div className="execution-warning">
              <strong>Inspection incomplete</strong>
              <ul>
                {result.unverified.map((item, index) => (
                  <li key={index}>
                    {item.revision}: {item.reason.replaceAll("_", " ")}
                    {item.baseline_path
                      ? ` · ${item.revision === "baseline" ? item.baseline_path : item.candidate_path}`
                      : ""}
                  </li>
                ))}
              </ul>
            </div>
          )}
        </>
      )}
      <details className="evidence-details">
        <summary>Inspection coverage & limits</summary>
        <ul>
          {result.limitations.map((item) => (
            <li key={item}>{item}</li>
          ))}
        </ul>
      </details>
    </section>
  );
}
