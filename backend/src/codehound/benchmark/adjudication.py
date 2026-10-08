"""Blinded AI reference adjudication, kept separate from human-ground-truth intake."""

import argparse
import hashlib
import json
import math
import re
from pathlib import Path

from codehound.benchmark.corpus_run import regular_bytes
from codehound.benchmark.run import evidence_writer
from codehound.execution.protocol import load_evidence

HASH = re.compile(r"[0-9a-f]{64}")
IDENTITY = ("case_id", "patch_id", "case_identity_sha256", "material_sha256")
VERDICTS = {"Correct", "Incorrect", "Unclear"}
CONFIDENCES = {"High", "Medium", "Low"}


def canonical_sha256(value):
    """Bind all delivered material and metadata, excluding only its own digest."""
    payload = {key: item for key, item in value.items() if key != "material_sha256"}
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    ).hexdigest()


def text(value, field, limit=4000):
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise ValueError(f"{field} must be a bounded nonempty string.")
    return value


def strings(value, field, *, nonempty=False):
    if not isinstance(value, list) or len(value) > 100 or (nonempty and not value):
        raise ValueError(f"{field} must be a bounded list.")
    for item in value:
        text(item, field)
    if len(set(value)) != len(value):
        raise ValueError(f"{field} cannot contain duplicate entries.")
    return value


def validate_bundle(bundle):
    required = {
        "schema_version",
        "case_id",
        "patch_id",
        "case_identity_sha256",
        "material_sha256",
        "materials",
        "neutral_evidence",
    }
    if (
        not isinstance(bundle, dict)
        or set(bundle) - required - {"metadata"}
        or required - set(bundle)
    ):
        raise ValueError("Unexpected or missing bundle fields.")
    if type(bundle["schema_version"]) is not int or bundle["schema_version"] != 1:
        raise ValueError("Unsupported bundle schema.")
    for name in IDENTITY[:2]:
        text(bundle[name], name, 300)
        if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,159}", bundle[name]) is None:
            raise ValueError("Case and patch identifiers must be safe filename components.")
    for name in IDENTITY[2:]:
        if not isinstance(bundle[name], str) or HASH.fullmatch(bundle[name]) is None:
            raise ValueError("Malformed bundle digest.")
    if bundle["material_sha256"] != canonical_sha256(bundle):
        raise ValueError("Bundle material digest differs.")
    if "metadata" in bundle:
        metadata = bundle["metadata"]
        if not isinstance(metadata, dict) or set(metadata) - {"producer", "limitations"}:
            raise ValueError("Unexpected bundle metadata.")
        if "producer" in metadata:
            text(metadata["producer"], "producer")
        if "limitations" in metadata:
            strings(metadata["limitations"], "limitations")
    ids, material_kinds, runtime = set(), set(), set()
    for collection in ("materials", "neutral_evidence"):
        items = bundle[collection]
        if not isinstance(items, list) or len(items) > 100:
            raise ValueError("Material inventory must be a bounded list.")
        for item in items:
            required_item = {"id", "kind", "sha256", "content"}
            if collection == "neutral_evidence":
                required_item.add("status")
            if (
                not isinstance(item, dict)
                or required_item - set(item)
                or set(item) - required_item - {"size_bytes"}
            ):
                raise ValueError("Unexpected material fields.")
            identifier = text(item["id"], "material id", 300)
            if identifier in ids:
                raise ValueError("Duplicate material/evidence identity.")
            ids.add(identifier)
            if not isinstance(item["sha256"], str) or HASH.fullmatch(item["sha256"]) is None:
                raise ValueError("Malformed material digest.")
            if not isinstance(item["content"], str) or len(item["content"]) > 2_000_000:
                raise ValueError("Material content must be bounded text.")
            content_bytes = item["content"].encode()
            if hashlib.sha256(content_bytes).hexdigest() != item["sha256"]:
                raise ValueError("Material content digest differs.")
            if "size_bytes" in item and (
                type(item["size_bytes"]) is not int or item["size_bytes"] != len(content_bytes)
            ):
                raise ValueError("Material byte length differs.")
            if collection == "materials":
                if not isinstance(item["kind"], str) or item["kind"] not in {
                    "issue",
                    "patch",
                    "code",
                }:
                    raise ValueError("Only issue, patch and code are review materials.")
                material_kinds.add(item["kind"])
            else:
                if (
                    item["kind"] != "runtime"
                    or not isinstance(item["status"], str)
                    or item["status"] not in {"completed", "unavailable"}
                ):
                    raise ValueError("Only explicitly neutral runtime evidence is permitted.")
                if item["status"] == "completed":
                    runtime.add(identifier)
    if not {"issue", "patch", "code"} <= material_kinds:
        raise ValueError("Bundle requires issue, patch and relevant code.")
    return ids, runtime


def validate_review(review, bundle):
    required = {
        "schema_version",
        *IDENTITY,
        "reviewer_id",
        "model_id",
        "context_id",
        "verdict",
        "confidence",
        "evidence",
        "verified_material_ids",
        "actually_verified",
        "uncertainty",
    }
    if not isinstance(review, dict) or set(review) != required:
        raise ValueError("Unexpected or missing review fields.")
    if type(review["schema_version"]) is not int or review["schema_version"] != 1:
        raise ValueError("Unsupported review schema.")
    if any(review[name] != bundle[name] for name in IDENTITY):
        raise ValueError("Review identity/material differs from the delivered bundle.")
    allowed, runtime = validate_bundle(bundle)
    for field in ("reviewer_id", "model_id", "context_id"):
        text(review[field], field, 300)
    if (
        not isinstance(review["verdict"], str)
        or review["verdict"] not in VERDICTS
        or not isinstance(review["confidence"], str)
        or review["confidence"] not in CONFIDENCES
    ):
        raise ValueError("Unexpected verdict or confidence.")
    inspected = set(
        strings(
            review["verified_material_ids"],
            "verified_material_ids",
            nonempty=review["verdict"] != "Unclear",
        )
    )
    if not inspected <= allowed:
        raise ValueError("Review claims inspection of material outside its bundle.")
    strings(review["actually_verified"], "actually_verified", nonempty=True)
    strings(review["uncertainty"], "uncertainty")
    evidence = review["evidence"]
    if not isinstance(evidence, list) or len(evidence) > 100:
        raise ValueError("Evidence must be a bounded list.")
    behavioral, failures = set(), set()
    for item in evidence:
        if not isinstance(item, dict) or set(item) != {
            "requirement",
            "claim",
            "evidence_ids",
            "failure_id",
        }:
            raise ValueError("Unexpected review evidence fields.")
        text(item["requirement"], "requirement")
        text(item["claim"], "claim")
        cited = set(strings(item["evidence_ids"], "evidence_ids"))
        if not cited <= inspected:
            raise ValueError("Cited evidence was not actually inspected.")
        concrete = cited & runtime
        behavioral.update(concrete)
        failure = item["failure_id"]
        if failure is not None:
            normalized = " ".join(text(failure, "failure_id", 1000).casefold().split())
            failures.update((normalized, identifier) for identifier in concrete)
    return behavioral, failures


def inventory(items, field):
    if not isinstance(items, list) or not 1 <= len(items) <= 10_000:
        raise ValueError(f"{field} requires a bounded nonempty list.")
    if any(
        not isinstance(item, dict) or not isinstance(item.get("case_id"), str) for item in items
    ):
        raise ValueError(f"{field} requires case identities.")
    result = {item["case_id"]: item for item in items}
    if len(result) != len(items):
        raise ValueError(f"{field} contains duplicate cases.")
    return result


def adjudicate(
    bundles,
    reviews_a,
    reviews_b,
    *,
    seed="pilot-2026-10-08",
    sample_rate=0.2,
    confidence_policy="high_only",
):
    """Compare independent reviews only after both complete; never create human labels."""
    text(seed, "sampling seed", 300)
    if not isinstance(confidence_policy, str) or confidence_policy not in {"high_only", "no_low"}:
        raise ValueError("Confidence policy must be high_only or no_low.")
    accepted_confidences = {"High"} if confidence_policy == "high_only" else {"High", "Medium"}
    if (
        type(sample_rate) not in {int, float}
        or not math.isfinite(sample_rate)
        or not 0.2 <= sample_rate <= 1
    ):
        raise ValueError("Agreement audit sampling must be between 20% and 100%.")
    materials = inventory(bundles, "bundles")
    slots = (inventory(reviews_a, "reviewer A"), inventory(reviews_b, "reviewer B"))
    if any(set(slot) != set(materials) for slot in slots):
        raise ValueError("Both reviewers must finish the complete material inventory.")
    rows, eligible, contexts = [], [], set()
    for case_id in sorted(materials):
        bundle, a, b = materials[case_id], slots[0][case_id], slots[1][case_id]
        evidence_a, failures_a = validate_review(a, bundle)
        evidence_b, failures_b = validate_review(b, bundle)
        for review in (a, b):
            if review["context_id"] in contexts:
                raise ValueError("Every patch review requires a fresh context across both cohorts.")
            contexts.add(review["context_id"])
        if any(a[field] == b[field] for field in ("reviewer_id", "model_id", "context_id")):
            raise ValueError("A/B require different reviewers, models and independent contexts.")
        reasons, label = [], None
        if a["verdict"] != b["verdict"]:
            reasons.append("reviewer_disagreement")
        if "Unclear" in {a["verdict"], b["verdict"]}:
            reasons.append("unclear_review")
        if any(item["confidence"] not in accepted_confidences for item in (a, b)):
            reasons.append(
                "not_high_confidence" if confidence_policy == "high_only" else "low_confidence"
            )
        if not evidence_a or not evidence_b:
            reasons.append("no_concrete_behavioral_evidence")
        core = {item["id"] for item in bundle["materials"] if item["kind"] in {"issue", "patch"}}
        code = {item["id"] for item in bundle["materials"] if item["kind"] == "code"}
        if any(
            not core <= set(review["verified_material_ids"])
            or not code.intersection(review["verified_material_ids"])
            for review in (a, b)
        ):
            reasons.append("not_all_core_materials_inspected")
        shared_failures = failures_a & failures_b
        if a["verdict"] == b["verdict"] == "Incorrect" and not shared_failures:
            reasons.append("no_shared_demonstrated_failure")
        if not reasons:
            label = a["verdict"].lower()
            eligible.append(case_id)
        rows.append(
            {
                **{name: bundle[name] for name in IDENTITY},
                "reference_label": label,
                "label_provenance": "provisional_ai_consensus" if label else None,
                "reviewer_a": a,
                "reviewer_b": b,
                "shared_failures": [
                    {"failure_id": failure, "evidence_id": identifier}
                    for failure, identifier in sorted(shared_failures)
                ],
                "human_review_required": bool(reasons),
                "human_review_reasons": reasons,
                "agreement_audit_selected": False,
                "reference_scoring_eligible": bool(label),
            }
        )
    ranked = sorted(
        eligible, key=lambda value: hashlib.sha256(f"{seed}\0{value}".encode()).hexdigest()
    )
    sampled = set(ranked[: math.ceil(len(ranked) * sample_rate)])
    for row in rows:
        if row["case_id"] in sampled:
            row["agreement_audit_selected"] = True
            row["human_review_required"] = True
            row["human_review_reasons"].append("random_agreement_audit_pending")
            row["reference_scoring_eligible"] = False
    return {
        "schema_version": 1,
        "kind": "blinded_ai_reference_adjudication",
        "label_population": "provisional_ai_consensus",
        "human_ground_truth": False,
        "confidence_policy": confidence_policy,
        "sampling": {
            "seed": seed,
            "rate": sample_rate,
            "eligible_agreements": len(eligible),
            "selected": len(sampled),
        },
        "rows": rows,
        "summary": {
            "cases": len(rows),
            "raw_agreements": sum(
                row["reviewer_a"]["verdict"] == row["reviewer_b"]["verdict"] for row in rows
            ),
            "disagreements": sum(
                row["reviewer_a"]["verdict"] != row["reviewer_b"]["verdict"] for row in rows
            ),
            "provisional_labels": len(eligible),
            "human_review_required": sum(row["human_review_required"] for row in rows),
            "reference_scoring_eligible": sum(row["reference_scoring_eligible"] for row in rows),
        },
        "limitations": [
            "AI agreement is a provisional reference and can share correlated model errors.",
            "Model/context identifiers are recorded attestations, not proof of isolation.",
            "Runtime citations are required; their interpretation still depends on reviewers.",
            "Random agreement audits remain excluded until separately reviewed by a human.",
        ],
    }


def reference_metrics(adjudication, validated_report):
    """Join an already validated CodeHound report after blinded reviews have completed."""
    if (
        adjudication.get("kind") != "blinded_ai_reference_adjudication"
        or adjudication.get("human_ground_truth") is not False
    ):
        raise ValueError("Only provisional AI adjudication can be scored here.")
    if validated_report.get("kind") != "validated_task_experiment_report":
        raise ValueError("Reference scoring requires a validated task experiment report.")
    confidence_policy = adjudication.get("confidence_policy", "high_only")
    if not isinstance(confidence_policy, str) or confidence_policy not in {"high_only", "no_low"}:
        raise ValueError("Confidence policy must be high_only or no_low.")
    accepted_confidences = {"High"} if confidence_policy == "high_only" else {"High", "Medium"}
    by_alias = inventory(adjudication["rows"], "adjudication")
    references = {row["patch_id"]: row for row in by_alias.values()}
    if len(references) != len(by_alias):
        raise ValueError("Reference aliases cannot duplicate an original patch identity.")
    decisions = inventory(validated_report["rows"], "CodeHound report")
    if set(references) != set(decisions):
        raise ValueError("Reference and CodeHound case inventories differ.")
    counts = dict.fromkeys(("tp", "fp", "fn", "tn"), 0)
    errors, evaluated, labels, abstentions = {"false_positives": [], "false_negatives": []}, 0, 0, 0
    total_decided = 0
    for case_id, reference in references.items():
        label = reference["reference_label"]
        if label not in {None, "correct", "incorrect"}:
            raise ValueError("Unexpected provisional reference label.")
        for flag in (
            "reference_scoring_eligible",
            "human_review_required",
            "agreement_audit_selected",
        ):
            if type(reference.get(flag)) is not bool:
                raise ValueError("Reference eligibility flags must be boolean.")
        if reference["reference_scoring_eligible"] != (
            label is not None
            and not reference["human_review_required"]
            and not reference["agreement_audit_selected"]
        ):
            raise ValueError("Pending or missing references cannot be scoring eligible.")
        if label is not None and (
            reference["label_provenance"] != "provisional_ai_consensus"
            or any(
                review["verdict"].lower() != label
                or review["confidence"] not in accepted_confidences
                or any(review[field] != reference[field] for field in IDENTITY)
                for review in (reference["reviewer_a"], reference["reviewer_b"])
            )
        ):
            raise ValueError("Reference label differs from its bound confidence-policy reviews.")
        decision = decisions[case_id]
        if reference["case_identity_sha256"] != decision.get("case_identity_sha256"):
            raise ValueError("Reference label binds a different patch identity.")
        prediction = decision.get("decisions", {}).get("task_behavior")
        if not isinstance(prediction, str) or prediction not in {"accept", "reject", "abstain"}:
            raise ValueError("Unexpected validated CodeHound decision.")
        total_decided += prediction != "abstain"
        if not reference["reference_scoring_eligible"]:
            continue
        labels += 1
        if prediction == "abstain":
            abstentions += 1
            continue
        evaluated += 1
        incorrect = reference["reference_label"] == "incorrect"
        rejected = prediction == "reject"
        category = (
            "tp" if incorrect and rejected else "fn" if incorrect else "fp" if rejected else "tn"
        )
        counts[category] += 1
        if category in {"fp", "fn"}:
            errors["false_positives" if category == "fp" else "false_negatives"].append(
                {"case_id": reference["case_id"], "patch_id": case_id}
            )
    tp, fp, fn, tn = (counts[key] for key in ("tp", "fp", "fn", "tn"))

    def ratio(numerator, denominator):
        return numerator / denominator if denominator else None

    return {
        "schema_version": 1,
        "kind": "provisional_reference_metrics",
        "label_population": "provisional_ai_consensus",
        "human_ground_truth": False,
        "confidence_policy": confidence_policy,
        "adjudication_sha256": canonical_sha256(adjudication),
        "validated_report_corpus_sha256": validated_report.get("corpus_sha256"),
        "cases": len(references),
        "scoring_eligible_reference_labels": labels,
        "reference_evaluated": evaluated,
        "reference_abstentions": abstentions,
        "all_case_coverage": ratio(total_decided, len(references)),
        "all_case_abstention_rate": ratio(len(references) - total_decided, len(references)),
        "reference_coverage": ratio(evaluated, labels),
        "reference_abstention_rate": ratio(abstentions, labels),
        "reference_label_coverage": ratio(labels, len(references)),
        "confusion": counts,
        "precision": ratio(tp, tp + fp),
        "recall": ratio(tp, tp + fn),
        "false_positive_rate": ratio(fp, fp + tn),
        "false_negative_rate": ratio(fn, fn + tp),
        "error_analysis": errors,
        "limitations": [
            "Accuracy uses eligible provisional AI references and non-abstaining decisions.",
            "These rates do not estimate human-reviewed correctness or full-corpus accuracy.",
            "Human disagreements, uncertainties and pending agreement audits are excluded.",
        ],
    }


def read_list(path, key):
    value = load_evidence(regular_bytes(path, 64 * 1024 * 1024), limit=64 * 1024 * 1024)
    if isinstance(value, dict):
        if (
            set(value) != {"schema_version", key}
            or type(value["schema_version"]) is not int
            or value["schema_version"] != 1
        ):
            raise ValueError("Unexpected input wrapper.")
        value = value[key]
    inventory(value, key)
    return value


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("consensus", "reference-metrics"))
    for name in ("bundles", "reviewer-a", "reviewer-b", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--seed", default="pilot-2026-10-08")
    parser.add_argument("--sample-rate", type=float, default=0.2)
    parser.add_argument("--confidence-policy", choices=("high_only", "no_low"), default="high_only")
    for name in ("corpus", "manifest", "human-reviews"):
        parser.add_argument("--" + name, type=Path)
    args = parser.parse_args()
    result = adjudicate(
        read_list(args.bundles, "bundles"),
        read_list(args.reviewer_a, "reviews"),
        read_list(args.reviewer_b, "reviews"),
        seed=args.seed,
        sample_rate=args.sample_rate,
        confidence_policy=args.confidence_policy,
    )
    if args.command == "reference-metrics":
        if any(value is None for value in (args.corpus, args.manifest, args.human_reviews)):
            parser.error("reference-metrics requires --corpus, --manifest and --human-reviews")
        from codehound.benchmark.task_report import report

        result = reference_metrics(result, report(args.corpus, args.manifest, args.human_reviews))
    evidence_writer(args.output)(result)
    print(f"Saved {result['kind']}: {args.output}")


if __name__ == "__main__":
    main()
