"""Trusted pytest entrypoint; structured evidence remains an in-process observation."""

import base64
import json
import sys

import pytest


class Reporter:
    def __init__(self):
        self.collected = []
        self.phases = {}
        self.collection_errors = []

    def pytest_collection_finish(self, session):
        self.collected = [item.nodeid for item in session.items]

    def pytest_collectreport(self, report):
        if report.failed:
            self.collection_errors.append(str(report.longrepr)[-2000:])

    def pytest_runtest_logreport(self, report):
        self.phases.setdefault(report.nodeid, []).append(report)

    def evidence(self, exit_code):
        tests = []
        for nodeid in self.collected:
            reports = self.phases.get(nodeid, [])
            outcome, message = "not_run", ""
            for report in reports:
                if report.failed:
                    outcome = "failed" if report.when == "call" else "error"
                    message = str(report.longrepr)[-2000:]
                elif report.skipped and outcome not in ("failed", "error"):
                    outcome = "xfailed" if hasattr(report, "wasxfail") else "skipped"
                    message = str(report.longrepr)[-2000:]
                elif report.when == "call" and report.passed:
                    outcome = "xpassed" if hasattr(report, "wasxfail") else "passed"
            tests.append(
                {
                    "nodeid": nodeid,
                    "outcome": outcome,
                    "duration_seconds": round(sum(r.duration for r in reports), 6),
                    "message": message,
                }
            )
        return {
            "schema_version": 1,
            "exit_code": int(exit_code),
            "collected": self.collected,
            "tests": tests,
            "collection_errors": self.collection_errors,
        }


def main():
    # Load trusted pytest before exposing candidate modules. This does not prevent
    # deliberate in-process monkeypatching; consumers must preserve that limitation.
    token = sys.argv[1]
    reporter = Reporter()
    sys.dont_write_bytecode = True
    sys.path.insert(0, "/workspace")
    sys.path.insert(0, "/workspace/src")
    code = pytest.main(
        [
            "-q",
            "-s",
            "-c",
            "/harness/pytest.ini",
            "--rootdir=/tests",
            "--confcutdir=/tests",
            "--import-mode=importlib",
            "-p",
            "no:cacheprovider",
            "/tests",
        ],
        plugins=[reporter],
    )
    payload = json.dumps(reporter.evidence(code), allow_nan=False, separators=(",", ":"))
    encoded = base64.b64encode(payload.encode()).decode()
    print(f"\nCODEHOUND_TEST_REPORT_V1:{token}:{encoded}", flush=True)
    raise SystemExit(code)


if __name__ == "__main__":
    main()
