import asyncio
import os
import shutil
from pathlib import Path

import pytest

from codehound.evaluation.registry import load_profiles
from codehound.execution.independent import IndependentRunner
from codehound.execution.results import summarize_comparisons


def test_verdict_profile_covers_public_contract_and_all_cases_are_mapped():
    profile = load_profiles()["codehound-verdict-contract"]
    for suite in (profile.visible, profile.hidden):
        for case in suite.cases:
            assert summarize_comparisons(*case.args) == case.expect.value
    references = {
        (reference.suite, reference.case_id)
        for requirement in profile.requirements
        for reference in requirement.cases
    }
    assert len(references) == 12
    assert profile.public()["visible_cases"] == 3 and profile.public()["hidden_cases"] == 9


@pytest.mark.skipif(
    not os.getenv("CODEHOUND_TEST_IMAGE_ID"), reason="Trusted Docker image required"
)
def test_operator_verdict_profile_runs_through_external_json_contract(tmp_path):
    profile = load_profiles()["codehound-verdict-contract"]
    # Copy only the target's source files. Never mount the developer checkout or its .env.
    source = Path(__file__).parents[1] / "src" / "codehound" / "execution"
    package = tmp_path / "backend" / "src" / "codehound" / "execution"
    package.mkdir(parents=True)
    for name in ("results.py", "protocol.py"):
        shutil.copyfile(source / name, package / name)
    tmp_path.chmod(0o755)

    async def run():
        runner = IndependentRunner(os.environ["CODEHOUND_TEST_IMAGE_ID"])
        for suite in (profile.visible, profile.hidden):
            result = await runner.run(tmp_path, suite)
            assert result.status == "completed" and result.exit_code == 0
            assert all(case["outcome"] == "passed" for case in result.test_report["tests"])
            assert result.evidence_source == "external_json_assertions"

    asyncio.run(run())
