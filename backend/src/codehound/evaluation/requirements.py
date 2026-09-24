"""Connect explicit operator requirements to evidence, without inferring issue coverage."""

from codehound.execution.results import validate_report


def case_outcomes(run):
    if (
        not run
        or not (
            run.get("status") == "completed"
            or (run.get("status") == "timeout" and run.get("exit_code") == 2)
        )
        or run.get("exit_code") not in (0, 1, 2)
        or run.get("evidence_error")
        or run.get("evidence_source") != "external_json_assertions"
    ):
        return {}
    try:
        validate_report(run["test_report"], run["exit_code"])
    except (ValueError, TypeError, KeyError):
        return {}
    return {case["nodeid"]: case["outcome"] for case in run["test_report"]["tests"]}


def state(outcomes):
    counts = {
        "passed": outcomes.count("passed"),
        "failed": outcomes.count("failed"),
        "unverified": sum(value not in ("passed", "failed") for value in outcomes),
    }
    status = (
        "unmapped"
        if not outcomes
        else "contradicted"
        if counts["failed"]
        else "unverified"
        if counts["unverified"]
        else "supported_by_checks"
    )
    return {"status": status, **counts}


def requirement_evidence(profile, artifact):
    inventories = {}
    for name, suite in artifact.get("suites", {}).items():
        before, after = suite.get("baseline", {}), suite.get("candidate", {})
        identity = ("image_id", "evaluator_sha256", "evidence_source")
        comparable = all(before.get(key) == after.get(key) and before.get(key) for key in identity)
        inventories[name] = {
            revision: case_outcomes(run) if comparable else {}
            for revision, run in (("baseline", before), ("candidate", after))
        }
    rows = []
    referenced = set()
    for requirement in profile.requirements:
        cases = []
        for reference in requirement.cases:
            suite = getattr(profile, reference.suite)
            nodeid = f"{suite.name}::{reference.case_id}"
            referenced.add((reference.suite, reference.case_id))
            cases.append(
                {
                    "suite": reference.suite,
                    "case_id": reference.case_id,
                    "nodeid": nodeid,
                    **{
                        revision: inventories.get(reference.suite, {})
                        .get(revision, {})
                        .get(nodeid, "not_run")
                        for revision in ("baseline", "candidate")
                    },
                }
            )
        rows.append(
            {
                "id": requirement.id,
                "description": requirement.description,
                "cases": cases,
                **{
                    revision: state([case[revision] for case in cases])
                    for revision in ("baseline", "candidate")
                },
            }
        )
    inventory = {
        (name, case.id)
        for name, suite in (("visible", profile.visible), ("hidden", profile.hidden))
        if suite
        for case in suite.cases
    }
    return {
        "schema_version": 1,
        "source": "operator_profile",
        "requirements": rows,
        "unmapped_cases": [
            {"suite": name, "case_id": identifier}
            for name, identifier in sorted(inventory - referenced)
        ],
        "limitations": [
            "Requirements and mappings were supplied by the operator, not inferred from the issue.",
            "Passing mapped examples supports only those observations; "
            "it does not prove a general requirement.",
            "Completeness of these requirements against the submitted issue remains unverified.",
        ],
    }
