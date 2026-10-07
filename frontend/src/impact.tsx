export type PythonImpact = {
  status: string;
  new_syntax_errors: { path: string; line: number; kind: string }[];
  revisions: Record<
    string,
    {
      python_files: number;
      parsed_files: number;
      resolved_edges: number;
      unresolved_imports: number;
      ambiguous_modules: number;
      affected_count: number;
      affected: {
        path: string;
        distance: number;
        import_chain: string[];
        import_chain_truncated: boolean;
      }[];
      affected_truncated: boolean;
      excluded_directories: string[];
    }
  >;
  unverified: { revision: string; path: string; reason: string }[];
  unverified_count: number;
  limitations: string[];
};
export function ImpactEvidence({ result }: { result: PythonImpact }) {
  return (
    <section className="suite-evidence">
      <h3>Python repository impact</h3>
      <p className="profile-coverage">
        Import relationships identify possible downstream effects. They do not
        establish that a component is broken.
      </p>
      {result.new_syntax_errors.length > 0 && (
        <div className="execution-warning">
          <strong>New syntax errors in the configured Python runtime</strong>
          <ul>
            {result.new_syntax_errors.map((item) => (
              <li key={item.path}>
                <code>
                  {item.path}:{item.line}
                </code>
              </li>
            ))}
          </ul>
        </div>
      )}
      {Object.entries(result.revisions).map(([name, revision]) => (
        <details className="evidence-details" key={name}>
          <summary>
            {name === "baseline" ? "Baseline" : "Candidate"} ·{" "}
            {revision.affected_count} possible downstream files
          </summary>
          <p className="muted">
            {revision.parsed_files}/{revision.python_files} Python files parsed
            · {revision.resolved_edges} resolved import relationships ·{" "}
            {revision.unresolved_imports} unresolved imports
          </p>
          {revision.affected.length ? (
            <ul className="impact-paths">
              {revision.affected.map((item) => (
                <li key={item.path}>
                  <strong>
                    <code>{item.path}</code>
                  </strong>{" "}
                  · {item.distance} import{" "}
                  {item.distance === 1 ? "step" : "steps"}
                  <code className="import-trail">
                    {(item.import_chain_truncated
                      ? [
                          ...item.import_chain.slice(0, -1),
                          "…",
                          item.import_chain.at(-1),
                        ]
                      : item.import_chain
                    ).join(" → ")}
                  </code>
                </li>
              ))}
            </ul>
          ) : (
            <p>
              No downstream paths found in the resolved import graph. Unresolved
              or dynamic dependencies remain unverified.
            </p>
          )}
          {revision.affected_truncated && (
            <p>Showing the first 100 downstream paths.</p>
          )}
          {revision.ambiguous_modules > 0 && (
            <p>
              {revision.ambiguous_modules} ambiguous module names were left
              unresolved.
            </p>
          )}
          <p className="muted">
            Excluded directories: {revision.excluded_directories.join(", ")}
          </p>
        </details>
      ))}
      {result.unverified_count > 0 && (
        <details className="evidence-details">
          <summary>
            {result.unverified_count} incomplete inspections or coverage limits
          </summary>
          <ul>
            {result.unverified.map((item, index) => (
              <li key={index}>
                {item.revision}: {item.reason.replaceAll("_", " ")}
                {item.path && (
                  <>
                    {" "}
                    · <code>{item.path}</code>
                  </>
                )}
              </li>
            ))}
          </ul>
          {result.unverified_count > result.unverified.length && (
            <p>Only the first {result.unverified.length} entries are shown.</p>
          )}
        </details>
      )}
      <details className="evidence-details">
        <summary>Analysis coverage & limits</summary>
        <ul>
          {result.limitations.map((item) => (
            <li key={item}>{item}</li>
          ))}
        </ul>
      </details>
    </section>
  );
}
