type State = {
  status: string;
  passed: number;
  failed: number;
  unverified: number;
};
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
    }[];
  }[];
  unmapped_cases: { suite: string; case_id: string }[];
  limitations: string[];
};
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
