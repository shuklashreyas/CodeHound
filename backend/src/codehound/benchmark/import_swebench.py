"""Import downloaded SWE-bench predictions as an unlabeled development pilot.

This module is offline: it never downloads, applies patches, or runs setup commands.
Case payloads exclude gold patches, test patches, resolved IDs, and upstream test
outcomes. Optional original attribution files are retained separately as sources.
"""

import argparse
import hashlib
import json
from collections import defaultdict, deque
from pathlib import Path

from codehound.benchmark.corpus import (
    MAX_ARTIFACT_BYTES,
    Case,
    Corpus,
    Source,
    prepare_corpus,
    read_retained_file,
)
from codehound.execution.protocol import load_evidence

MAX_INPUT_BYTES = 32 * 1024 * 1024
MAX_ROWS = 10000
SELECTION_RULE = (
    "Repository-balanced round-robin: sort repositories and instance IDs lexicographically; "
    "take one available instance per repository per round until the requested count. "
    "Eligibility requires matching task metadata and a nonempty published patch. "
    "No test outcomes, resolved labels, gold patches, or patch correctness inform selection. "
    "All cases are development; this public pilot is not a held-out evaluation."
)


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def read_input(path):
    path = Path(path).absolute()
    return read_retained_file(path.parent, path.name, max_bytes=MAX_INPUT_BYTES)


def records(raw):
    """Accept JSON arrays or JSONL, rejecting ambiguous duplicate keys and rows."""
    stripped = raw.lstrip()
    if stripped.startswith(b"["):
        rows = load_evidence(raw, limit=MAX_INPUT_BYTES)
        if not isinstance(rows, list):
            raise ValueError("Input must contain a JSON array or JSONL objects.")
    else:
        rows = []
        for line in raw.splitlines():
            if line.strip():
                rows.append(load_evidence(line, limit=MAX_INPUT_BYTES))
                if len(rows) > MAX_ROWS:
                    raise ValueError("Input exceeds the row limit.")
    if not rows or len(rows) > MAX_ROWS:
        raise ValueError("Input must contain 1 through 10000 rows.")
    result = {}
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get("instance_id"), str):
            raise ValueError("Each row requires an instance_id string.")
        identifier = row["instance_id"]
        if identifier in result:
            raise ValueError("Duplicate instance_id; select one prediction run per import.")
        result[identifier] = row
    return result


def import_predictions(
    predictions_path,
    tasks_path,
    output,
    *,
    predictions_source,
    tasks_source,
    agent,
    count=50,
    model=None,
    tasks_original_path=None,
    additional_sources=(),
    name="SWE-bench published agent patch development pilot",
):
    """Verify source hashes, select without outcomes, and retain only issue/patch text.

    When tasks are normalized from parquet, tasks_source identifies the original
    parquet bytes and tasks_original_path is mandatory. The sidecar separately
    records the JSON normalization hash; it never calls that hash a parquet hash.
    The caller supplies normalized metadata; this importer does not verify the
    semantic conversion from parquet, which is explicitly recorded as a limit.
    """
    if type(count) is not int or not 1 <= count <= 1000:
        raise ValueError("Requested count must be between 1 and 1000.")
    if not isinstance(agent, str) or not 1 <= len(agent) <= 200:
        raise ValueError("Provide the upstream-reported agent name.")
    output = Path(output).absolute()
    if output.exists():
        raise ValueError("Output already exists; choose a new corpus directory.")
    predictions_source = Source.model_validate(predictions_source)
    tasks_source = Source.model_validate(tasks_source)
    if predictions_source.id == tasks_source.id:
        raise ValueError("Task and prediction sources require distinct IDs.")
    predictions_raw = read_input(predictions_path)
    tasks_raw = read_input(tasks_path)
    original_raw = read_input(tasks_original_path) if tasks_original_path else tasks_raw
    if digest(predictions_raw) != predictions_source.sha256:
        raise ValueError("Prediction source SHA256 mismatch.")
    if digest(original_raw) != tasks_source.sha256:
        raise ValueError("Task source SHA256 mismatch; normalized input needs its original source.")
    retained_sources = []
    for source, path in additional_sources:
        source = Source.model_validate(source)
        raw = read_input(path)
        if digest(raw) != source.sha256:
            raise ValueError("Additional source SHA256 mismatch.")
        retained_sources.append((source, raw))
    predictions = records(predictions_raw)
    tasks = records(tasks_raw)
    groups = defaultdict(list)
    excluded = {"empty_patch": 0, "missing_task": 0}
    for identifier, prediction in predictions.items():
        patch = prediction.get("model_patch")
        if not isinstance(patch, str):
            raise ValueError("Prediction model_patch must be a string.")
        if not patch.strip():
            excluded["empty_patch"] += 1
            continue
        if identifier not in tasks:
            excluded["missing_task"] += 1
            continue
        task = tasks[identifier]
        if not isinstance(task.get("repo"), str):
            raise ValueError("Matching task requires a repository string.")
        groups[task["repo"]].append(identifier)
    queues = {repo: deque(sorted(ids)) for repo, ids in groups.items()}
    eligible_count = sum(map(len, queues.values()))
    if eligible_count < count:
        raise ValueError(f"Only {eligible_count} eligible predictions; requested {count}.")
    chosen = []
    while len(chosen) < count:
        for repository in sorted(queues):
            if queues[repository]:
                chosen.append(queues[repository].popleft())
            if len(chosen) == count:
                break
    cases, payloads = [], []
    for identifier in chosen:
        task, prediction = tasks[identifier], predictions[identifier]
        issue, patch = task.get("problem_statement"), prediction["model_patch"]
        predicted_model = prediction.get("model_name_or_path")
        if not isinstance(issue, str) or not issue.strip():
            raise ValueError("Selected task requires a nonempty issue string.")
        if not isinstance(predicted_model, str) or not predicted_model:
            raise ValueError("Selected prediction requires model_name_or_path attribution.")
        if model is not None and predicted_model != model:
            raise ValueError("Selected prediction model differs from the requested attribution.")
        issue_bytes, patch_bytes = issue.encode("utf-8"), patch.encode("utf-8")
        if max(len(issue_bytes), len(patch_bytes)) > MAX_ARTIFACT_BYTES:
            raise ValueError("Selected issue or patch exceeds the retained artifact limit.")
        case = Case.model_validate(
            {
                "id": identifier,
                "task_id": identifier,
                "family": task["repo"],
                "split": "development",
                "repository": task["repo"],
                "base_sha": task.get("base_commit"),
                "issue_path": f"cases/{identifier}/issue.txt",
                "issue_sha256": digest(issue_bytes),
                "patch_path": f"cases/{identifier}/patch.diff",
                "patch_sha256": digest(patch_bytes),
                "generation": {
                    "kind": "published_agent_prediction",
                    "model": predicted_model,
                    "agent": agent,
                    "source_id": predictions_source.id,
                },
            }
        )
        cases.append(case)
        payloads.append((case, issue_bytes, patch_bytes))
    corpus = Corpus(
        name=name,
        selection=SELECTION_RULE,
        sources=[tasks_source, predictions_source, *[s for s, _ in retained_sources]],
        cases=cases,
    )
    provenance = {
        "schema_version": 1,
        "selection_rule": SELECTION_RULE,
        "requested_count": count,
        "selected_count": len(cases),
        "input_prediction_count": len(predictions),
        "input_task_count": len(tasks),
        "tasks_without_prediction": len(tasks.keys() - predictions.keys()),
        "eligible_nonempty_matching_count": eligible_count,
        "excluded": excluded,
        "eligible_not_selected": eligible_count - len(cases),
        "normalized_tasks_sha256": digest(tasks_raw),
        "original_tasks_source": tasks_source.model_dump(mode="json"),
        "normalization": "Select instance_id, repo, base_commit, problem_statement; JSON/JSONL.",
        "normalization_semantics_verified_by_importer": tasks_original_path is None,
        "prediction_authorship": "Upstream-reported; not independently authenticated.",
        "human_ground_truth": "Pending; upstream resolved labels are not human review labels.",
        "payload_redistribution_license": "Not established by this importer; retain locally.",
    }
    output.mkdir(parents=True)
    if retained_sources:
        (output / "sources").mkdir()
        for source, raw in retained_sources:
            (output / "sources" / (source.id + ".txt")).write_bytes(raw)
    for case, issue, patch in payloads:
        directory = output / "cases" / case.id
        directory.mkdir(parents=True)
        (output / case.issue_path).write_bytes(issue)
        (output / case.patch_path).write_bytes(patch)
    manifest = output / "corpus.json"
    manifest.write_text(corpus.model_dump_json(indent=2) + "\n", encoding="utf-8")
    (output / "import-provenance.json").write_text(
        json.dumps(provenance, sort_keys=True, indent=2) + "\n", encoding="utf-8"
    )
    prepare_corpus(manifest)
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--predictions", required=True, type=Path)
    parser.add_argument("--tasks", required=True, type=Path)
    parser.add_argument("--tasks-original", type=Path)
    parser.add_argument(
        "--additional-source",
        action="append",
        type=Path,
        default=[],
        help="Local JSON descriptor {source: {id,url,revision,sha256}, path: downloaded_file}.",
    )
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--count", type=int, default=50)
    parser.add_argument("--agent", required=True)
    parser.add_argument("--model")
    parser.add_argument("--name", default="SWE-bench published agent patch development pilot")
    for prefix in ("predictions", "tasks"):
        parser.add_argument(f"--{prefix}-url", required=True)
        parser.add_argument(f"--{prefix}-revision", required=True)
        parser.add_argument(f"--{prefix}-sha256", required=True)
    args = parser.parse_args()

    def source(prefix):
        return {
            "id": prefix,
            "url": getattr(args, prefix + "_url"),
            "revision": getattr(args, prefix + "_revision"),
            "sha256": getattr(args, prefix + "_sha256"),
        }

    try:
        additional_sources = []
        for path in args.additional_source:
            descriptor = load_evidence(read_input(path), limit=MAX_INPUT_BYTES)
            if not isinstance(descriptor, dict) or set(descriptor) != {"source", "path"}:
                raise ValueError("Additional source descriptor requires source and path.")
            if not isinstance(descriptor["path"], str):
                raise ValueError("Additional source descriptor path must be a string.")
            additional_sources.append((descriptor["source"], descriptor["path"]))
        manifest = import_predictions(
            args.predictions,
            args.tasks,
            args.output,
            predictions_source=source("predictions"),
            tasks_source=source("tasks"),
            agent=args.agent,
            model=args.model,
            count=args.count,
            name=args.name,
            tasks_original_path=args.tasks_original,
            additional_sources=additional_sources,
        )
    except (ValueError, OSError) as error:
        parser.error(str(error))
    print(f"Imported {args.count} unlabeled development cases to {manifest}")


if __name__ == "__main__":
    main()
