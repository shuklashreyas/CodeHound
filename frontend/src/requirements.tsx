type State = {
  status: string;
  passed: number;
  failed: number;
  unverified: number;
};
type Preview = { text: string; truncated: boolean };
type Observation = { status: string; response?: Preview };
export type RequirementEvidence = {
  source: string;
  requirements: {
    id: string;
    description: string;
    baseline: State;
    candidate: State;
    cases: {
      suite: string;
      case_id: string;
      baseline: string;
      candidate: string;
      target?: { module: string; function: string; source_directory: string };
      diagnostics?: {
        expectation: {
          visibility: string;
          kind?: string;
          text?: string;
          truncated?: boolean;
        };
        baseline: Observation;
        candidate: Observation;
      };
    }[];
  }[];
  unmapped_cases: { suite: string; case_id: string }[];
  limitations: string[];
};
function ObservationValue({
  label,
  value,
}: {
  label: string;
  value: Observation;
}) {
  return (
    <div className="case-observation">
      <strong>{label}</strong>
      {value.status === "observed" && value.response ? (
        <>
          <pre>{value.response.text}</pre>
          {value.response.truncated && (
            <small>Preview truncated; inspect raw execution evidence.</small>
          )}
        </>
      ) : (
        <p>No validated response available.</p>
      )}
    </div>
  );
}
const labels: Record<string, string> = {
  supported_by_checks: "Mapped examples pass",
  contradicted: "Contradicted by checks",
  unverified: "Evidence incomplete",
  unmapped: "No checks mapped",
};
function EvidenceState({ value }: { value: State }) {
  return (
    <span
      className={`badge ${value.status === "contradicted" ? "fail" : "not-run"}`}
    >
      {labels[value.status] || "Unverified"}
    </span>
  );
}
export function Requirements({ result }: { result: RequirementEvidence }) {
  return (
    <section className="suite-evidence">
      <h3>Requirement evidence</h3>
      <p className="profile-coverage">
        These requirements were defined in the operator profile. Coverage of
        your submitted issue remains unverified.
      </p>
      {!result.requirements.length && (
        <p>No requirements have been mapped to this profile.</p>
      )}
      {result.requirements.map((item) => (
        <details className="evidence-details" key={item.id}>
          <summary>{item.description}</summary>
          <dl>
            <dt>Baseline</dt>
            <dd>
              <EvidenceState value={item.baseline} />
            </dd>
            <dt>Candidate</dt>
            <dd>
              <EvidenceState value={item.candidate} />
            </dd>
          </dl>
          {item.cases.length > 0 ? (
            <ul className="requirement-cases">
              {item.cases.map((test) => (
                <li key={`${test.suite}:${test.case_id}`}>
                  <code>{test.case_id}</code> ·{" "}
                  {test.suite === "hidden" ? "independent" : "visible"}
                  <span>
                    {test.baseline.replaceAll("_", " ")} →{" "}
                    {test.candidate.replaceAll("_", " ")}
                  </span>
                  {test.target && (
                    <p className="muted">
                      Configured target:{" "}
                      <code>
                        {test.target.module}.{test.target.function}
                      </code>{" "}
                      · source root <code>{test.target.source_directory}</code>
                    </p>
                  )}
                  {test.diagnostics && (
                    <details className="evidence-details">
                      <summary>Inspect case evidence · {test.case_id}</summary>
                      <div className="case-observation">
                        <strong>Expected behavior</strong>
                        {test.diagnostics.expectation.visibility === "shown" ? (
                          <>
                            <pre>{test.diagnostics.expectation.text}</pre>
                            {test.diagnostics.expectation.truncated && (
                              <small>Expected-value preview truncated.</small>
                            )}
                          </>
                        ) : (
                          <p>
                            Independent expected answer withheld. The evaluator
                            checks the{" "}
                            {test.diagnostics.expectation.kind === "exception"
                              ? "expected exception"
                              : "expected return value"}{" "}
                            outside the candidate container.
                          </p>
                        )}
                      </div>
                      <ObservationValue
                        label="Baseline response"
                        value={test.diagnostics.baseline}
                      />
                      <ObservationValue
                        label="Candidate response"
                        value={test.diagnostics.candidate}
                      />
                    </details>
                  )}
                </li>
              ))}
            </ul>
          ) : (
            <p>This requirement has no checks; it has not been verified.</p>
          )}
        </details>
      ))}
      {result.unmapped_cases.length > 0 && (
        <p className="muted">
          {result.unmapped_cases.length} configured checks are not mapped to a
          requirement.
        </p>
      )}
      <details className="evidence-details">
        <summary>Requirement coverage & limits</summary>
        <ul>
          {result.limitations.map((item) => (
            <li key={item}>{item}</li>
          ))}
        </ul>
      </details>
    </section>
  );
}
