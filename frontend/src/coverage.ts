// Product capabilities, not verdicts for a selected patch.
export const implementedCoverage: Record<string, string> = {
  "Visible tests":
    "Operator-defined Python function cases run on both revisions. The entire repository test suite is not automatically discovered.",
  "Hidden tests":
    "Expected answers are retained outside candidate containers. Bundled dogfooding fixtures are public, so they are not secret tests.",
  "Regression safety":
    "Configured case outcomes are compared before and after the patch. Untested behavior remains unverified.",
  "Test integrity":
    "Changed Python tests are parsed for removed tests, changed assertions, and added skips. Findings require review.",
  "Requirement adherence":
    "Operator-defined requirements map to execution evidence. Complete coverage of the submitted issue is not established.",
  "Static analysis":
    "Bounded Python syntax checks run on both revisions. General lint, type, and security analysis are not implemented.",
  "Downstream impact":
    "Static Python import trails identify possible affected files. Dynamic dependencies and other languages remain unverified.",
};
