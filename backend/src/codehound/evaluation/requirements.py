"""Connect explicit operator requirements to evidence, without inferring issue coverage."""

import json

from codehound.execution.provenance import bind_source
from codehound.execution.results import validate_report

_SOURCE_BINDING = bind_source(__file__)


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


def preview(value):
    """Bound display-only JSON while keeping truncation explicit."""
    text = json.dumps(value, ensure_ascii=True, allow_nan=False, sort_keys=True)
    return {"text": text[:2000], "truncated": len(text) > 2000}


def observation_detail(run, case_id, outcome):
    if outcome == "not_run":
        return {"status": "unavailable"}
    matches = [
        item.get("observation")
        for item in run.get("case_evidence") or []
        if item.get("case_id") == case_id
    ]
    if len(matches) != 1 or not isinstance(matches[0], dict):
        return {"status": "unavailable"}
    observation = matches[0]
    response = observation.get("call_response")
    if (
        observation.get("evidence_error")
        or observation.get("status") != "completed"
        or observation.get("exit_code") != 0
        or not isinstance(response, dict)
        or response.get("kind") not in ("returned", "raised")
    ):
        return {"status": "unavailable"}
    return {"status": "observed", "response": preview(response)}


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
            case = next(item for item in suite.cases if item.id == reference.case_id)
            nodeid = f"{suite.name}::{reference.case_id}"
            referenced.add((reference.suite, reference.case_id))
            outcomes = {
                revision: inventories.get(reference.suite, {})
                .get(revision, {})
                .get(nodeid, "not_run")
                for revision in ("baseline", "candidate")
            }
            cases.append(
                {
                    "suite": reference.suite,
                    "case_id": reference.case_id,
                    "nodeid": nodeid,
                    **outcomes,
                    "target": {
                        "module": suite.module,
                        "function": suite.function,
                        "source_directory": suite.source_directory,
                    },
                    "diagnostics": {
                        "expectation": (
                            {"visibility": "shown", **preview(case.expect.model_dump())}
                            if reference.suite == "visible"
                            else {"visibility": "withheld", "kind": case.expect.kind}
                        ),
                        **{
                            revision: observation_detail(
                                artifact.get("suites", {})
                                .get(reference.suite, {})
                                .get(revision, {}),
                                reference.case_id,
                                outcomes[revision],
                            )
                            for revision in ("baseline", "candidate")
                        },
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
            "Independent expected answers are withheld from diagnostics; "
            "observed candidate responses are not trusted as proof of internal execution.",
        ],
    }
