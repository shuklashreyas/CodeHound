"""Normalize pinned Verified metadata locally; requires optional operator pyarrow."""

import argparse
import hashlib
import json
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument(
        "--spec",
        type=Path,
        default=Path(__file__).with_name("verified-mini-pilot.json"),
    )
    args = parser.parse_args()
    spec = json.loads(args.spec.read_text())
    if args.output.exists():
        parser.error("Output exists; choose a new normalization file.")
    if args.input.stat().st_size > 32 * 1024 * 1024:
        parser.error("Original dataset exceeds 32 MiB.")
    raw = args.input.read_bytes()
    if hashlib.sha256(raw).hexdigest() != spec["sources"]["tasks"]["sha256"]:
        parser.error("Original parquet SHA256 mismatch.")
    from pyarrow import BufferReader, parquet

    rows = parquet.read_table(
        BufferReader(raw), columns=spec["normalization"]["columns"]
    ).to_pylist()
    if len(rows) != 500:
        parser.error("Expected 500 official Verified task rows.")
    normalized = b"".join(
        (json.dumps(row, sort_keys=True, ensure_ascii=False) + "\n").encode("utf-8")
        for row in rows
    )
    if hashlib.sha256(normalized).hexdigest() != spec["normalization"]["output_sha256"]:
        parser.error(
            "Normalization SHA256 differs from the frozen pilot source specification."
        )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(normalized)
    print(
        f"Normalized {len(rows)} tasks; SHA256 {hashlib.sha256(normalized).hexdigest()}"
    )


if __name__ == "__main__":
    main()
