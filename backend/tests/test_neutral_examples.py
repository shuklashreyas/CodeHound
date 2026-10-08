"""Collector mechanics tests; target repository code is never imported."""

import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase
from unittest.mock import patch

from codehound.benchmark import neutral_examples as collector


class NeutralExamplesTests(IsolatedAsyncioTestCase):
    async def test_collects_both_raw_transcripts_without_interpreting_outputs(self):
        events = []

        class Workspace:
            def __init__(self, repository, base, head, **kwargs):
                events.append(("checkout", repository, base, head))

            async def __aenter__(self):
                return SimpleNamespace(baseline=Path("/unused-data-source"))

            async def __aexit__(self, *args):
                return None

        class Runner:
            def __init__(self, image_id, **kwargs):
                self.image_id = image_id

            async def run_container(self, workspace, mounts, command):
                events.append(("execute", workspace.name, command))
                return SimpleNamespace(
                    status="completed",
                    exit_code=1,
                    stdout=workspace.name + "\n",
                    stderr="raw exception\n",
                    duration_seconds=0.2,
                    image_id=self.image_id,
                    output_truncated=False,
                    oom_killed=False,
                    timeout_seconds=30,
                    network="none",
                    memory_mb=512,
                    cpu_limit=1.0,
                )

        def copy_data(source, destination):
            destination.mkdir()

        async def apply_data(workspace, raw):
            events.append(("patch", workspace.name, raw))

        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            case = root / "case-2401d837c5f37c9d"
            case.mkdir()
            issue, candidate_patch = b"issue reproduction", b"candidate patch data"
            (case / "issue.txt").write_bytes(issue)
            (case / "patch.diff").write_bytes(candidate_patch)
            metadata = {
                "repository": "sympy/sympy",
                "base_sha": "a" * 40,
                "issue_sha256": hashlib.sha256(issue).hexdigest(),
                "patch_sha256": hashlib.sha256(candidate_patch).hexdigest(),
            }
            (case / "baseline.json").write_text(json.dumps(metadata))
            with (
                patch.object(collector, "GitWorkspace", Workspace),
                patch.object(collector, "ContainerRunner", Runner),
                patch.object(collector, "copy_revision", copy_data),
                patch.object(collector, "apply_patch", apply_data),
            ):
                output = await collector.collect_case(case, root / "output")
            record = json.loads(output.read_text())
            self.assertEqual(record["gaps"], [])
            self.assertEqual(set(record["executions"]), {"baseline", "candidate"})
            for role in ("baseline", "candidate"):
                run = record["executions"][role]
                self.assertEqual(run["stdout"], role + "\n")
                self.assertEqual(run["stderr"], "raw exception\n")
                self.assertEqual(run["exit_code"], 1)
                self.assertIn("sys.path.insert(0, '/workspace')", run["source"])
                self.assertIn("assert _origin.is_relative_to", run["source"])
                self.assertEqual(run["command"], ["python", "-c", run["source"]])
            self.assertEqual(
                record["executions"]["baseline"]["source"],
                record["executions"]["candidate"]["source"],
            )
            self.assertEqual(events[1][0], "patch")
            self.assertEqual([e[1] for e in events if e[0] == "execute"], ["baseline", "candidate"])

    async def test_input_hash_mismatch_stops_before_checkout(self):
        with TemporaryDirectory() as temporary:
            case = Path(temporary) / "case-2401d837c5f37c9d"
            case.mkdir()
            (case / "issue.txt").write_text("issue")
            (case / "patch.diff").write_text("patch")
            (case / "baseline.json").write_text(
                json.dumps(
                    {
                        "repository": "sympy/sympy",
                        "base_sha": "a" * 40,
                        "issue_sha256": "b" * 64,
                        "patch_sha256": "c" * 64,
                    }
                )
            )
            with patch.object(collector, "GitWorkspace") as checkout:
                with self.assertRaisesRegex(ValueError, "hashes"):
                    await collector.collect_case(case, Path(temporary) / "output")
                checkout.assert_not_called()

    async def test_existing_artifact_is_not_overwritten_or_executed(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            output = root / "output"
            output.mkdir()
            existing = output / "case-2401d837c5f37c9d.json"
            existing.write_bytes(b"frozen evidence bytes\n")
            with patch.object(collector, "GitWorkspace") as checkout:
                with self.assertRaises(FileExistsError):
                    await collector.collect_case(root / "case-2401d837c5f37c9d", output)
                checkout.assert_not_called()
            self.assertEqual(existing.read_bytes(), b"frozen evidence bytes\n")

    async def test_cli_requires_new_destination(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            output = root / "already-existing"
            output.mkdir()
            args = SimpleNamespace(
                case=["case-2401d837c5f37c9d"],
                inputs=root / "inputs",
                output=output,
                timeout=30,
                concurrency=2,
            )
            with patch.object(collector, "collect_case") as collect:
                with self.assertRaises(FileExistsError):
                    await collector.main_async(args)
                collect.assert_not_called()
