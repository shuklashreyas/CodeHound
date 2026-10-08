# Pinned public pilot sources

`verified-mini-pilot.json` identifies one official SWE-bench Verified task snapshot,
one published mini-SWE-agent prediction run, and the official submission metadata.
Predictions and task metadata are matched by instance ID. Fifty nonempty matching
predictions are selected by repository round-robin using only repository/instance
identities. Every case is development, with human ground truth pending.

The metadata file includes an upstream aggregate score. It is retained as source
attribution only, is not used by selection, and must not enter blind human review
packets. Gold patches, reference test patches, resolved-instance lists, and test
outcomes are excluded from normalized task metadata and imported case payloads.
The `gemini-3-5-flash-fair` model string is preserved exactly from prediction rows;
the submission metadata identifies mini-SWE-agent version 2.4.2.

Payload redistribution rights are not established by the source material examined.
Keep downloaded sources and imported issue/patch payloads under ignored `data/`.
The MIT license of a benchmark harness does not establish a license for third-party
issues or generated patches. The source specification records the examined
dataset card and prediction repository. Committed files contain code, references,
hashes, and the workflow only.

## Reproduce the inputs

From the repository root, download these three exact URLs from the source spec;
no repository clone or candidate setup is needed. For example:

```bash
mkdir -p data/ai-patch-sources-2026-10-07
curl --fail --location --max-time 60 --max-filesize 33554432 \
  'https://raw.githubusercontent.com/SWE-bench/20260901_mini-v2.4.2_gemini-3-5-flash/2f6637a2557da1f7b5f2b583ab7090a077bbeed2/all_preds.jsonl' \
  --output data/ai-patch-sources-2026-10-07/predictions.jsonl
curl --fail --location --max-time 60 --max-filesize 33554432 \
  'https://huggingface.co/datasets/princeton-nlp/SWE-bench_Verified/resolve/c104f840cc67f8b6eec6f759ebc8b2693d585d4a/data/test-00000-of-00001.parquet' \
  --output data/ai-patch-sources-2026-10-07/tasks.parquet
curl --fail --location --max-time 60 --max-filesize 33554432 \
  'https://raw.githubusercontent.com/SWE-bench/experiments/40f164d5b8f1d249bf95a6df8b74b577fd8e519d/evaluation/verified/20260901_mini-v2.4.2_gemini-3-5-flash/metadata.yaml' \
  --output data/ai-patch-sources-2026-10-07/agent-metadata.yaml
```

Normalize metadata using a temporary operator environment. PyArrow is a data reader,
not a product dependency. The normalizer verifies original parquet and normalized
JSONL hashes against the source specification. It selects four metadata columns,
preserves parquet row order, serializes each object with sorted keys and
`ensure_ascii=False`, and appends one newline per record. It never retains gold
patches, reference tests, or outcomes in the normalized output.

```bash
python3 -m venv data/ai-patch-source-reader
data/ai-patch-source-reader/bin/python -m pip install pyarrow==25.0.1
data/ai-patch-source-reader/bin/python benchmarks/sources/normalize_verified.py \
  --input data/ai-patch-sources-2026-10-07/tasks.parquet \
  --output data/ai-patch-sources-2026-10-07/tasks.jsonl
```

## Import offline

The following command uses only local downloaded files. It verifies prediction,
original parquet, and attribution metadata hashes; preserves issue/patch text bytes;
validates the resulting corpus; and refuses to replace an existing output directory.
The sidecar records the normalized JSONL hash separately from the original parquet
source hash. The importer records that semantic parquet normalization is external;
the normalizer above verifies its exact expected output hash.

```bash
PYTHONPATH=backend/src backend/.venv/bin/python - <<'PY'
import json
from pathlib import Path
from codehound.benchmark.import_swebench import import_predictions

spec = json.loads(Path('benchmarks/sources/verified-mini-pilot.json').read_text())
root = Path('data/ai-patch-sources-2026-10-07').absolute()
sources = spec['sources']
manifest = import_predictions(
    root / 'predictions.jsonl', root / 'tasks.jsonl',
    Path('data/ai-patch-pilot-2026-10-07'),
    predictions_source=sources['predictions'], tasks_source=sources['tasks'],
    tasks_original_path=root / 'tasks.parquet',
    additional_sources=[(sources['metadata'], root / 'agent-metadata.yaml')],
    agent=spec['generation']['agent'], model=spec['generation']['model_name_or_path'],
    count=spec['selection']['count'],
)
print(manifest)
PY
```

This imports an unlabeled corpus, not a measured accuracy result. Existing public
SWE-bench cases may have been exposed during model training. Human labels,
reviewer disagreement, evaluator coverage, and unsupported task counts must be
reported separately before using the corpus to estimate detection performance.
