"""Raw issue-based demonstrations; deliberately contains no adjudication labels.

Only case issue.txt, baseline.json and patch.diff supply task semantics. The
sources below were authored from issue text before opening candidate patches.
Candidate imports happen solely in restricted ContainerRunner containers.
"""

import argparse
import asyncio
import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from codehound.benchmark.corpus_run import apply_patch, copy_revision
from codehound.execution.docker import ContainerRunner
from codehound.repositories.checkout import GitWorkspace

IMAGES = {
    "sympy/sympy": "sha256:547195083cd5ff07b9ec0805e50ba9fb18daae712e6130983876d0c63060de44",
    "psf/requests": "sha256:c78974014c55f457701ef17fad5cd311d1a5c885eb082114949709db5a73f624",
}
REQUEST_CAPTURE = """import requests

def capture(self, request, **kwargs):
    print("method", repr(request.method), type(request.method).__name__)
    print("headers", sorted(request.headers.items()))
    print("body", repr(request.body), type(request.body).__name__)
    response = requests.Response()
    response.status_code = 200
    response.request = request
    return response
requests.sessions.Session.send = capture
"""
GAPS = {
    "case-b18a9eb41855e79c": (
        "Issue reproduction requires Python 2.7.2 transport concatenation and a remote "
        "multipart POST; pinned image provides Python 3.9.25. "
        "No execution authored for incompatible runtime."
    ),
    "case-d99b41b21d1a19b0": (
        "Issue provides prose and a curl command requiring remote server Digest challenges, "
        "but no Python reproduction snippet. No offline raw wire request execution authored."
    ),
}
EXAMPLES = {
    "case-2401d837c5f37c9d": (
        """from sympy.utilities.lambdify import implemented_function
f = implemented_function('f', lambda x: x ** 2)
g = implemented_function('g', lambda x: 2 * x)
print(f(2).evalf())
print(g(2).evalf())
print(f(g(2)).evalf())
""",
        "Issue composition example",
        [],
    ),
    "case-3a2ed197961376e6": (
        """from sympy import Point
print(Point(2, 0).distance(Point(1, 0, 2)))
""",
        "Issue mixed-dimensional Point distance example",
        ["Added explicit Point import and print for transcript expression."],
    ),
    "case-4445b26a9a77d3a3": (
        """from sympy.combinatorics import Permutation
class DerivedPermutation(Permutation):
    pass
p = DerivedPermutation([1, 0])
print(type(p).__module__, type(p).__name__)
print(repr(p))
q = DerivedPermutation._af_new([1, 0])
print(type(q).__module__, type(q).__name__)
print(repr(q))
""",
        "Issue describes inability to construct a Permutation subclass",
        [
            "Issue has no runnable snippet; minimal subclass construction and named "
            "_af_new entry point derived from its description."
        ],
    ),
    "case-517b802baf0b58ff": (
        """from sympy.combinatorics import Permutation
p = Permutation([[0, 1], [0, 1]])
print(repr(p))
print(p.array_form)
""",
        "Issue non-disjoint cycles constructor expression",
        ["Added import and printed representation and array form."],
    ),
    "case-595eb0dddb324256": (
        """from sympy import *
from sympy import Q as Query
n = Symbol('n', integer=True, positive=True)
i, j = symbols('i j', integer=True)
M = MatrixSymbol('M', n, n)
e = None
with assuming(Query.orthogonal(M)):
    e = refine((M.T * M).doit())
print(e, e[0, 0], e[0, 1], e[1, 0], e[1, 1])
print(ask(Query.diagonal(e)), ask(Query.integer_elements(e)))
print(Sum(e[i, i], (i, 0, n-1)).doit())
print(Sum(Sum(e[i, j], (i, 0, n-1)), (j, 0, n-1)).doit())
""",
        "Issue identity-matrix summation reproduction",
        ["Removed issue comments describing answers."],
    ),
    "case-4705f06c571ac9d4": (
        REQUEST_CAPTURE
        + """requests.request(method=b'GET', url='http://example.invalid/')
""",
        "Issue bytes GET method description",
        [
            "Offline Session.send replacement prints prepared request; no server response "
            "evidence. Python runtime may differ from issue Python 3.4."
        ],
    ),
    "case-8aea118cd59c2931": (
        REQUEST_CAPTURE
        + """session = requests.Session()
session.headers['Accept-Encoding'] = None
session.get('http://example.invalid/')
""",
        "Issue setting session Accept-Encoding to None",
        [
            "Added offline preparation using Session.send capture; "
            "cannot establish transmitted wire header."
        ],
    ),
    "case-dc835b7ee6bb1a9d": (
        REQUEST_CAPTURE
        + """requests.get('http://example.invalid/')
""",
        "Issue requests.get without body and generated Content-Length",
        ["Offline Session.send capture; no Amazon or other server request."],
    ),
    "case-f654e88f6a919259": (
        REQUEST_CAPTURE
        + """requests.put('http://httpbin.org/put', data=u'ööö'.encode('utf-8'))
""",
        "Issue binary PUT payload reproduction",
        ["Offline Session.send capture substitutes for remote transport."],
    ),
}


def sha256(raw):
    return hashlib.sha256(raw).hexdigest()


def execution_source(repository, source):
    module = {"sympy/sympy": "sympy", "psf/requests": "requests"}[repository]
    return (
        "import sys, pathlib\nsys.path.insert(0, '/workspace')\n"
        f"import {module} as _target\n"
        "_origin = pathlib.Path(_target.__file__).resolve()\n"
        "assert _origin.is_relative_to(pathlib.Path('/workspace')), str(_origin)\n"
        "print('module_origin', str(_origin))\n"
        "print('python_version', sys.version)\n" + source
    )


def raw_execution(result, command, source):
    # Retain lifecycle fields only; no evaluator reports, judgments or controllers.
    fields = (
        "status",
        "exit_code",
        "stdout",
        "stderr",
        "duration_seconds",
        "image_id",
        "output_truncated",
        "oom_killed",
        "timeout_seconds",
        "network",
        "memory_mb",
        "cpu_limit",
    )
    return {**{key: getattr(result, key) for key in fields}, "command": command, "source": source}


async def collect_case(case_dir, output_dir, *, timeout_seconds=30):
    case_id = case_dir.name
    path = output_dir / f"{case_id}.json"
    # Refuse collisions before materialization or execution, including symlinks.
    if path.exists() or path.is_symlink():
        raise FileExistsError(f"Neutral artifact already exists: {path}")
    meta = json.loads((case_dir / "baseline.json").read_text())
    issue = (case_dir / "issue.txt").read_bytes()
    source, basis, adaptations = EXAMPLES.get(
        case_id, ("", "Issue lacks locally supported reproduction", [])
    )
    # Source selected before candidate patch is read. No other case files read.
    patch = (case_dir / "patch.diff").read_bytes()
    image = IMAGES[meta["repository"]]
    record = {
        "schema_version": 1,
        "case_id": case_id,
        "repository": meta["repository"],
        "base_sha": meta["base_sha"],
        "issue_sha256": sha256(issue),
        "patch_sha256": sha256(patch),
        "image_id": image,
        "example": {
            "source": source,
            "source_sha256": sha256(source.encode()),
            "issue_basis": basis,
            "adaptations": adaptations,
        },
        "executions": {},
        "gaps": [],
    }
    if (
        record["issue_sha256"] != meta["issue_sha256"]
        or record["patch_sha256"] != meta["patch_sha256"]
    ):
        raise ValueError("Input packet hashes do not match baseline.json")
    if case_id in GAPS:
        record["gaps"].append({"stage": "authoring", "detail": GAPS[case_id]})
        output_dir.mkdir(parents=True, exist_ok=True)
        with path.open("x") as stream:
            stream.write(json.dumps(record, indent=2, ensure_ascii=False) + "\n")
        return path
    command_source = execution_source(meta["repository"], source)
    command = ["python", "-c", command_source]
    try:
        async with GitWorkspace(
            meta["repository"],
            meta["base_sha"],
            meta["base_sha"],
            max_bytes=1024**3,
            max_files=200000,
        ) as checkout:
            with TemporaryDirectory(prefix="codehound-neutral-") as temporary:
                root = Path(temporary)
                baseline, candidate = root / "baseline", root / "candidate"
                await asyncio.to_thread(copy_revision, checkout.baseline, baseline)
                await asyncio.to_thread(copy_revision, checkout.baseline, candidate)
                await apply_patch(candidate, patch)
                runner = ContainerRunner(image, timeout_seconds=timeout_seconds)
                for name, workspace in [("baseline", baseline), ("candidate", candidate)]:
                    result = await runner.run_container(workspace, [], command)
                    record["executions"][name] = raw_execution(result, command, command_source)
    except Exception as exc:
        record["gaps"].append(
            {"stage": "materialize_or_execute", "type": type(exc).__name__, "detail": str(exc)}
        )
    output_dir.mkdir(parents=True, exist_ok=True)
    with path.open("x") as stream:
        stream.write(json.dumps(record, indent=2, ensure_ascii=False) + "\n")
    return path


async def main_async(args):
    selected = args.case or list(EXAMPLES) + list(GAPS)
    if len(selected) != len(set(selected)):
        raise ValueError("Each case may be collected only once per destination.")
    # Each CLI collection owns a fresh destination. No frozen evidence is reused.
    args.output.mkdir(parents=True, exist_ok=False)
    semaphore = asyncio.Semaphore(args.concurrency)

    async def one(case_id):
        async with semaphore:
            path = await collect_case(
                args.inputs / case_id, args.output, timeout_seconds=args.timeout
            )
            print(path, flush=True)
            return path

    paths = await asyncio.gather(*(one(case_id) for case_id in selected))
    artifacts = []
    for path in sorted(paths):
        raw = path.read_bytes()
        record = json.loads(raw)
        artifacts.append(
            {
                "case_id": record["case_id"],
                "artifact_path": str(path.resolve()),
                "artifact_sha256": sha256(raw),
                "execution_roles": list(record["executions"]),
                "gaps": record["gaps"],
            }
        )
    with (args.output / "manifest.json").open("x") as stream:
        stream.write(json.dumps({"schema_version": 1, "artifacts": artifacts}, indent=2) + "\n")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inputs", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--case", action="append", choices=sorted(set(EXAMPLES) | set(GAPS)))
    parser.add_argument("--timeout", type=int, default=30)
    parser.add_argument("--concurrency", type=int, default=2)
    asyncio.run(main_async(parser.parse_args()))


if __name__ == "__main__":
    main()
