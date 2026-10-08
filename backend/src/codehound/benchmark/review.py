"""Blinded human review packets and identity-bound ground truth intake."""

import argparse
import json
from datetime import datetime
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator, model_validator

from codehound.benchmark.corpus import (
    MAX_CORPUS_BYTES,
    Contract,
    Digest,
    Identifier,
    PreparedCase,
    prepare_corpus,
    read_retained_file,
    strict_json,
)
from codehound.execution.provenance import bind_source

_SOURCE_BINDING = bind_source(__file__)

ATTESTATION = "I personally reviewed this patch against the task."
FailureCategory = Literal[
    "regression", "incomplete_fix", "task_mismatch", "test_manipulation", "other"
]


class HumanReview(Contract):
    schema_version: Literal[1] = 1
    case_id: Identifier
    case_identity_sha256: Digest
    reviewer: str = Field(min_length=2, max_length=200)
    reviewed_at: datetime
    verdict: Literal["valid", "invalid", "uncertain"]
    failure_categories: list[FailureCategory] = Field(default_factory=list, max_length=5)
    rationale: str = Field(min_length=20, max_length=12000)
    attestation: Literal["I personally reviewed this patch against the task."]

    @field_validator("reviewer", "rationale")
    @classmethod
    def meaningful_text(cls, value):
        if not value.strip() or value != value.strip():
            raise ValueError("Human review text must be nonblank and trimmed.")
        return value

    @field_validator("reviewed_at")
    @classmethod
    def timestamp_has_timezone(cls, value):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("reviewed_at must include its timezone.")
        return value

    @model_validator(mode="after")
    def consistent_verdict(self):
        if len(set(self.failure_categories)) != len(self.failure_categories):
            raise ValueError("Failure categories must be unique.")
        if self.verdict == "valid" and self.failure_categories:
            raise ValueError("Valid patches must not have failure categories.")
        if self.verdict == "invalid" and not self.failure_categories:
            raise ValueError("Invalid patches require a failure category.")
        return self


class ReviewCollection(Contract):
    schema_version: Literal[1] = 1
    reviews: list[HumanReview] = Field(max_length=10000)


def load_reviews(path: Path) -> tuple[HumanReview, ...]:
    path = Path(path).absolute()
    raw = read_retained_file(path.parent, path.name, max_bytes=MAX_CORPUS_BYTES)
    strict_json(raw, limit=MAX_CORPUS_BYTES)
    return tuple(ReviewCollection.model_validate_json(raw).reviews)


def blinded_case_id(prepared: PreparedCase) -> str:
    return "case-" + prepared.identity_sha256[:16]


def join_reviews(
    prepared: tuple[PreparedCase, ...], reviews: tuple[HumanReview, ...]
) -> list[dict]:
    """Stale/orphan records fail; missing, uncertainty and conflicts are excluded."""
    by_identity = {item.identity_sha256: item for item in prepared}
    grouped: dict[str, list[HumanReview]] = {identity: [] for identity in by_identity}
    reviewers: set[tuple[str, str]] = set()
    for review in reviews:
        item = by_identity.get(review.case_identity_sha256)
        if item is None or review.case_id not in (item.case.id, blinded_case_id(item)):
            raise ValueError(f"Stale or unknown review identity for {review.case_id}.")
        reviewer_key = (review.case_identity_sha256, review.reviewer.casefold())
        if reviewer_key in reviewers:
            raise ValueError("Duplicate reviewer for the same patch identity.")
        reviewers.add(reviewer_key)
        grouped[item.identity_sha256].append(review)
    rows = []
    for item in prepared:
        records = grouped[item.identity_sha256]
        verdicts = {record.verdict for record in records}
        label = "unreviewed"
        if not records:
            status = "unreviewed"
        elif "uncertain" in verdicts:
            status = "uncertain"
        elif len(verdicts) > 1:
            status = "conflicting"
        else:
            status = "agreed"
            label = records[0].verdict
        rows.append(
            {
                "case_id": item.case.id,
                "case_identity_sha256": item.identity_sha256,
                "label": label,
                "review_status": status,
                "accuracy_eligible": status == "agreed",
                "reviews": [record.model_dump(mode="json") for record in records],
            }
        )
    return rows


def export_packet(corpus_path: Path, destination: Path) -> dict:
    """Export retained task/patch bytes and blank forms, omitting producer/results."""
    _, _, prepared = prepare_corpus(corpus_path)
    destination = Path(destination)
    if destination.exists():
        raise ValueError("Review packet destination must not already exist.")
    destination.mkdir(parents=True)
    templates = []
    for item in prepared:
        alias = blinded_case_id(item)
        folder = destination / alias
        folder.mkdir()
        (folder / "issue.txt").write_bytes(item.issue)
        (folder / "patch.diff").write_bytes(item.patch)
        (folder / "baseline.json").write_text(
            json.dumps(
                {
                    "repository": item.case.repository,
                    "base_sha": item.case.base_sha,
                    "case_identity_sha256": item.identity_sha256,
                    "issue_sha256": item.case.issue_sha256,
                    "patch_sha256": item.case.patch_sha256,
                },
                indent=2,
            )
            + "\n"
        )
        templates.append(
            {
                "schema_version": 1,
                "case_id": alias,
                "case_identity_sha256": item.identity_sha256,
                "reviewer": "",
                "reviewed_at": "",
                "verdict": "",
                "failure_categories": [],
                "rationale": "",
                "attestation": "",
            }
        )
    (destination / "reviews.template.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "reviews": templates,
            },
            indent=2,
        )
        + "\n"
    )
    (destination / "README.txt").write_text(
        "Human review packet\n\n"
        "Producer metadata and evaluator results are omitted. Task and patch contents may "
        "still reveal identity; this packet cannot guarantee anonymity. Attestation is a "
        "self-report; software cannot authenticate that a reviewer is a human.\n\n"
        "Review the task, baseline commit and patch independently. Record whether the patch "
        "satisfies the complete task and preserves existing behavior. You may inspect or run "
        "the baseline and patched repository in an isolated environment. Record concrete "
        "evidence and remaining uncertainty in the rationale. Do not use generator identity, "
        "upstream outcomes or CodeHound decisions to decide the verdict.\n\n"
        "Copy reviews.template.json to reviews.json and complete only the entries you "
        "personally review. Use your name, timezone-qualified ISO datetime, valid/invalid/"
        "uncertain verdict, and a rationale of at least 20 characters. Invalid patches require "
        "one or more failure categories: regression, incomplete_fix, task_mismatch, "
        "test_manipulation, other. Valid patches have no failure categories.\n"
        f"After actual human review, attest exactly: {ATTESTATION}\n"
        "Leave incomplete forms out of reviews.json. Conflicting, uncertain and missing "
        "reviews are excluded from accuracy calculations.\n"
    )
    return {"packet": str(destination), "cases": len(prepared), "human_reviews_completed": 0}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    packet = commands.add_parser("packet", help="Export a blinded human review packet")
    packet.add_argument("corpus", type=Path)
    packet.add_argument("destination", type=Path)
    for name in ("validate", "join"):
        command = commands.add_parser(name)
        command.add_argument("corpus", type=Path)
        command.add_argument("reviews", type=Path)
    args = parser.parse_args(argv)
    try:
        if args.command == "packet":
            result = export_packet(args.corpus, args.destination)
        else:
            _, _, prepared = prepare_corpus(args.corpus)
            rows = join_reviews(prepared, load_reviews(args.reviews))
            result = (
                rows
                if args.command == "join"
                else {
                    "cases": len(rows),
                    "accuracy_eligible": sum(row["accuracy_eligible"] for row in rows),
                    "excluded": sum(not row["accuracy_eligible"] for row in rows),
                }
            )
    except (ValueError, OSError) as exc:
        parser.exit(2, f"error: {exc}\n")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
