"""Bounded Python import inventory. Parses source; never imports repository modules."""

import ast
import base64
import hashlib
import json
import os
import sys
from pathlib import Path

PREFIX = "CODEHOUND_PYTHON_REPOSITORY_V1:"
EXCLUDED = {".git", ".venv", "venv", "node_modules", "__pycache__", "dist", "build", ".tox"}
MAX_FILES = 2000
MAX_BYTES = 16 * 1024 * 1024


def scan(root):
    files, notices = [], []
    total = 0
    stop = False

    def walk_error(error):
        try:
            path = Path(error.filename).relative_to(root).as_posix()
        except (TypeError, ValueError):
            path = ""
        notices.append({"path": path if path != "." else "", "reason": "unreadable_directory"})

    for parent, directories, names in os.walk(root, followlinks=False, onerror=walk_error):
        directories[:] = sorted(name for name in directories if name not in EXCLUDED)
        for name in list(directories):
            path = Path(parent) / name
            if path.is_symlink():
                directories.remove(name)
                notices.append(
                    {"path": path.relative_to(root).as_posix(), "reason": "symlink_directory"}
                )
        for name in sorted(names):
            if not name.endswith(".py"):
                continue
            path = Path(parent) / name
            relative = path.relative_to(root).as_posix()
            if len(files) >= MAX_FILES:
                notices.append({"path": "", "reason": "file_limit"})
                stop = True
                break
            item = {"path": relative}
            files.append(item)
            try:
                if path.is_symlink():
                    item["status"] = "symlink"
                    continue
                size = path.stat().st_size
                if size > 1024 * 1024:
                    item["status"] = "too_large"
                    continue
                total += size
                if total > MAX_BYTES:
                    item["status"] = "budget_exceeded"
                    notices.append({"path": "", "reason": "byte_limit"})
                    stop = True
                    break
                source = path.read_bytes()
                tree = ast.parse(source, filename=relative)
                nodes = list(ast.walk(tree))
                if len(nodes) > 50000:
                    item["status"] = "too_complex"
                    continue
                imports = []
                for node in nodes:
                    if isinstance(node, ast.Import):
                        imports.extend(
                            {"module": alias.name, "names": [], "level": 0, "line": node.lineno}
                            for alias in node.names
                        )
                    elif isinstance(node, ast.ImportFrom):
                        imports.append(
                            {
                                "module": node.module or "",
                                "names": [alias.name for alias in node.names],
                                "level": node.level,
                                "line": node.lineno,
                            }
                        )
                if len(imports) > 1000:
                    item["status"] = "too_complex"
                    continue
                item.update(
                    status="parsed", sha256=hashlib.sha256(source).hexdigest(), imports=imports
                )
            except SyntaxError as exc:
                item.update(status="syntax_error", line=exc.lineno or 1)
            except (OSError, UnicodeError):
                item["status"] = "unreadable"
            except (ValueError, RecursionError):
                item["status"] = "too_complex"
        if stop:
            break
    report = {
        "schema_version": 1,
        "files": files,
        "notices": notices[:200],
        "notices_truncated": len(notices) > 200,
        "excluded_directories": sorted(EXCLUDED),
    }
    # Keep the complete-report protocol bounded even for import-heavy repositories.
    if len(json.dumps(report).encode()) > 650000:
        report["files"] = []
        report["notices"] = [{"path": "", "reason": "report_limit"}]
    return report


def main():
    payload = json.dumps(scan(Path("/workspace")), separators=(",", ":")).encode()
    print(PREFIX + sys.argv[1] + ":" + base64.b64encode(payload).decode())


if __name__ == "__main__":
    main()
