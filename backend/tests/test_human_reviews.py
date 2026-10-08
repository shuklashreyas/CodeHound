import json
from datetime import datetime, timezone

import pytest
from pydantic import ValidationError
from test_corpus import make_corpus

from codehound.benchmark.corpus import prepare_corpus
from codehound.benchmark.review import (
    ATTESTATION,
    HumanReview,
    blinded_case_id,
    export_packet,
    join_reviews,
    load_reviews,
    main,
)


def review(item, *, reviewer="Human reviewer", verdict="valid", **changes):
    data = {
        "case_id": item.case.id,
        "case_identity_sha256": item.identity_sha256,
        "reviewer": reviewer,
        "reviewed_at": datetime(2026, 1, 1, tzinfo=timezone.utc),
        "verdict": verdict,
        "failure_categories": ["incomplete_fix"] if verdict == "invalid" else [],
        "rationale": "A human-reviewed test fixture with concrete evidence.",
        "attestation": ATTESTATION,
    }
    return HumanReview.model_validate(data | changes)


def test_missing_uncertain_and_conflicting_review_excluded(tmp_path):
    path, _ = make_corpus(tmp_path)
    _, _, prepared = prepare_corpus(path)
    item = prepared[0]
    assert join_reviews(prepared, ())[0]["review_status"] == "unreviewed"
    uncertain = review(item, verdict="uncertain")
    assert join_reviews(prepared, (uncertain,))[0]["label"] == "unreviewed"
    conflict = (review(item), review(item, reviewer="Second human", verdict="invalid"))
    result = join_reviews(prepared, conflict)[0]
    assert result["review_status"] == "conflicting"
    assert result["accuracy_eligible"] is False
    agreement = (review(item), review(item, reviewer="Second human"))
    result = join_reviews(prepared, agreement)[0]
    assert result["label"] == "valid" and result["accuracy_eligible"]


def test_stale_or_orphan_review_rejected_instead_of_silently_scored(tmp_path):
    path, _ = make_corpus(tmp_path)
    _, _, prepared = prepare_corpus(path)
    for changes in ({"case_identity_sha256": "e" * 64}, {"case_id": "nonexistent"}):
        with pytest.raises(ValueError, match="Stale or unknown"):
            join_reviews(prepared, (review(prepared[0], **changes),))
    with pytest.raises(ValueError, match="Duplicate reviewer"):
        join_reviews(prepared, (review(prepared[0]), review(prepared[0])))


def test_retained_review_collection_and_blank_packet_templates(tmp_path):
    path, _ = make_corpus(tmp_path)
    _, _, prepared = prepare_corpus(path)
    packet = tmp_path / "review-packet"
    result = export_packet(path, packet)
    assert result["human_reviews_completed"] == 0
    template = json.loads((packet / "reviews.template.json").read_text())
    assert template["reviews"][0]["case_id"] == blinded_case_id(prepared[0])
    packet_text = "\n".join(file.read_text() for file in packet.rglob("*") if file.is_file())
    assert "model-private" not in packet_text
    assert "agent-private" not in packet_text
    assert "producer-case" not in packet_text
    with pytest.raises(ValidationError):
        load_reviews(packet / "reviews.template.json")
    completed = review(prepared[0], case_id=blinded_case_id(prepared[0]))
    reviews_path = tmp_path / "reviews.json"
    reviews_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "reviews": [completed.model_dump(mode="json")],
            }
        )
    )
    assert join_reviews(prepared, load_reviews(reviews_path))[0]["label"] == "valid"
    with pytest.raises(ValueError, match="already exist"):
        export_packet(path, packet)


@pytest.mark.parametrize(
    "changes",
    [
        {"attestation": "automatically imported from resolved label"},
        {"reviewer": "  "},
        {"rationale": "Too short"},
        {"reviewed_at": datetime(2026, 1, 1)},
        {"verdict": "valid", "failure_categories": ["regression"]},
        {"verdict": "invalid", "failure_categories": []},
    ],
)
def test_review_requires_explicit_named_attested_consistent_evidence(tmp_path, changes):
    path, _ = make_corpus(tmp_path)
    _, _, prepared = prepare_corpus(path)
    with pytest.raises(ValidationError):
        review(prepared[0], **changes)


def test_review_cli_validate_reports_exclusions(tmp_path, capsys):
    path, _ = make_corpus(tmp_path)
    reviews = tmp_path / "reviews.json"
    reviews.write_text('{"schema_version":1,"reviews":[]}')
    main(["validate", str(path), str(reviews)])
    assert json.loads(capsys.readouterr().out) == {
        "cases": 1,
        "accuracy_eligible": 0,
        "excluded": 1,
    }


def test_review_collection_rejects_duplicate_json_keys(tmp_path):
    path = tmp_path / "reviews.json"
    path.write_text('{"schema_version":1,"reviews":[],"reviews":[]}')
    with pytest.raises(ValueError, match="Duplicate JSON key"):
        load_reviews(path)
