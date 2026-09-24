"""Parse candidate test files without importing them. Executed only in a restricted container."""

import ast
import base64
import hashlib
import json
import sys
from pathlib import Path, PurePosixPath

MAX_FILE_BYTES = 1024 * 1024
MAX_TOTAL_BYTES = 8 * 1024 * 1024
MAX_NODES = 50000
MAX_ITEMS = 2000
ASSERT_METHODS = {
    "assertEqual",
    "assertNotEqual",
    "assertTrue",
    "assertFalse",
    "assertIs",
    "assertIsNot",
    "assertIsNone",
    "assertIsNotNone",
    "assertIn",
    "assertNotIn",
    "assertIsInstance",
    "assertNotIsInstance",
    "assertRaises",
    "assertRaisesRegex",
    "assertWarns",
    "assertWarnsRegex",
    "assertAlmostEqual",
    "assertNotAlmostEqual",
    "assertGreater",
    "assertGreaterEqual",
    "assertLess",
    "assertLessEqual",
    "assertRegex",
    "assertNotRegex",
    "assertCountEqual",
    "assertSequenceEqual",
    "assertListEqual",
    "assertTupleEqual",
    "assertSetEqual",
    "assertDictEqual",
    "assertMultiLineEqual",
    "assertLogs",
    "assertNoLogs",
}


def dotted(node):
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return dotted(node.value) + "." + node.attr
    return ""


def fingerprint(node):
    return hashlib.sha256(ast.dump(node, include_attributes=False).encode()).hexdigest()


class Structure(ast.NodeVisitor):
    def __init__(self):
        self.scope = []
        self.functions = []
        self.assertions = []
        self.skips = []

    def item(self, node):
        return {"scope": ".".join(self.scope), "line": node.lineno, "signature": fingerprint(node)}

    def visit_ClassDef(self, node):
        self.scope.append(node.name)
        self.generic_visit(node)
        self.scope.pop()

    def visit_FunctionDef(self, node):
        self.scope.append(node.name)
        self.functions.append(
            {
                "scope": ".".join(self.scope),
                "line": node.lineno,
                "is_test": node.name.startswith("test"),
            }
        )
        self.generic_visit(node)
        self.scope.pop()

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_Assert(self, node):
        item = self.item(node)
        # Assertion failure-message edits do not change the tested predicate.
        item["signature"] = fingerprint(node.test)
        self.assertions.append(item)
        self.generic_visit(node)

    def visit_Call(self, node):
        name = dotted(node.func)
        if (
            name.startswith("self.")
            and name[5:] in ASSERT_METHODS
            or name in {"pytest.raises", "pytest.warns"}
        ):
            self.assertions.append(self.item(node))
        if name in {
            "pytest.skip",
            "pytest.xfail",
            "pytest.mark.skip",
            "pytest.mark.skipif",
            "pytest.mark.xfail",
            "unittest.skip",
            "unittest.skipIf",
            "unittest.skipUnless",
            "unittest.expectedFailure",
        }:
            self.skips.append(self.item(node))
        self.generic_visit(node)

    def visit_Attribute(self, node):
        if dotted(node) in {"pytest.mark.skip", "pytest.mark.xfail", "unittest.expectedFailure"}:
            # Also covers decorators without parentheses. Calls are deduplicated below.
            self.skips.append(self.item(node))
        self.generic_visit(node)


def inspect_source(content):
    tree = ast.parse(content)
    if sum(1 for _ in ast.walk(tree)) > MAX_NODES:
        return {"status": "too_complex"}
    visitor = Structure()
    visitor.visit(tree)
    # Calls and their function attributes share the same scope/line. Prefer the
    # complete call, which also fingerprints conditions and expected failures.
    skips = {}
    for item in visitor.skips:
        skips.setdefault((item["scope"], item["line"]), item)
    if max(len(visitor.functions), len(visitor.assertions), len(skips)) > MAX_ITEMS:
        return {"status": "too_complex"}
    scopes = [item["scope"] for item in visitor.functions]
    if len(scopes) != len(set(scopes)):
        return {"status": "ambiguous_symbols"}
    return {
        "status": "parsed",
        "functions": visitor.functions,
        "assertions": visitor.assertions,
        "skips": list(skips.values()),
    }


def inspect_files(root, paths):
    results = []
    consumed = 0
    for name in paths:
        item = {"path": name}
        relative = PurePosixPath(name)
        if not name or relative.is_absolute() or ".." in relative.parts or "\x00" in name:
            results.append(item | {"status": "unsafe_path"})
            continue
        path = root / name
        if any(
            root.joinpath(*relative.parts[:index]).is_symlink()
            for index in range(1, len(relative.parts) + 1)
        ):
            results.append(item | {"status": "symlink"})
            continue
        try:
            if not path.is_file():
                results.append(item | {"status": "missing"})
                continue
            if path.stat().st_size > MAX_FILE_BYTES:
                results.append(item | {"status": "too_large"})
                continue
            if consumed + path.stat().st_size > MAX_TOTAL_BYTES:
                results.append(item | {"status": "budget_exceeded"})
                continue
            content = path.read_bytes()
            consumed += len(content)
            try:
                result = inspect_source(content)
            except (SyntaxError, UnicodeError, ValueError):
                result = {"status": "syntax_error"}
            except (RecursionError, MemoryError):
                result = {"status": "too_complex"}
            results.append(item | {"sha256": hashlib.sha256(content).hexdigest()} | result)
        except OSError:
            results.append(item | {"status": "unreadable"})
    return results


def main():
    request = json.loads(sys.stdin.readline(65537))
    paths = request["paths"]
    if (
        not isinstance(paths, list)
        or len(paths) > 300
        or any(not isinstance(p, str) or len(p) > 2048 for p in paths)
    ):
        raise ValueError("Invalid path inventory")
    result = {"schema_version": 1, "files": inspect_files(Path("/workspace"), paths)}
    payload = base64.b64encode(json.dumps(result, allow_nan=False).encode()).decode()
    print("CODEHOUND_STRUCTURE_V1:" + sys.argv[1] + ":" + payload, flush=True)


if __name__ == "__main__":
    main()
