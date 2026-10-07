"""Trusted bounded Ruff harness. Repository source is data, never imported."""

import base64
import hashlib
import io
import json
import os
import selectors
import stat
import subprocess
import sys
import tempfile
import time
import tokenize
from pathlib import Path

if __name__ != "__main__":
    # The controller imports shared constants; the isolated container needs only stdlib.
    from codehound.execution.provenance import bind_source

    _SOURCE_BINDING = bind_source(__file__)

PREFIX = "CODEHOUND_STATIC_V1:"
VERSION = "0.11.13"
FLAGS = [
    "check",
    "--isolated",
    "--no-cache",
    "--no-fix",
    "--ignore-noqa",
    "--no-respect-gitignore",
    "--select",
    "E,F",
    "--target-version",
    "py311",
    "--line-length",
    "100",
    "--output-format",
    "json",
]
LIMITS = {
    "files": 2000,
    "entries": 20000,
    "file_bytes": 1024 * 1024,
    "source_bytes": 16 * 1024 * 1024,
    "findings": 2000,
    "report_bytes": 700000,
    "timeout_seconds": 30,
}
EXCLUDED = {".git"}


def safe_path(value):
    return (
        isinstance(value, str)
        and 0 < len(value) <= 4096
        and not value.startswith("/")
        and "\\" not in value
        and all(part not in ("", ".", "..") for part in value.split("/"))
        and all(ord(char) >= 32 and ord(char) != 127 for char in value)
    )


def bounded_command(command, *, seconds=20, limit=700000):
    """Drain both pipes without retaining more than the report budget."""
    process = subprocess.Popen(
        command,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd="/tmp",
        env={"PATH": "/usr/local/bin:/usr/bin:/bin", "HOME": "/tmp"},
    )
    output, errors = bytearray(), bytearray()
    deadline = time.monotonic() + seconds
    with selectors.DefaultSelector() as selector:
        selector.register(process.stdout, selectors.EVENT_READ, output)
        selector.register(process.stderr, selectors.EVENT_READ, errors)
        try:
            while selector.get_map():
                if time.monotonic() > deadline:
                    raise ValueError("ruff_timeout")
                for key, _ in selector.select(timeout=0.1):
                    chunk = os.read(key.fileobj.fileno(), 8192)
                    if not chunk:
                        selector.unregister(key.fileobj)
                        continue
                    if len(output) + len(errors) + len(chunk) > limit:
                        raise ValueError("ruff_output_limit")
                    key.data.extend(chunk)
            process.wait(timeout=max(0.1, deadline - time.monotonic()))
        finally:
            if process.poll() is None:
                process.kill()
            process.wait()
            process.stdout.close()
            process.stderr.close()
    return process.returncode, bytes(output), bytes(errors)


def scan(root, destination):
    files, skipped, sources = [], [], {}
    total = entries = 0

    def notice(path, reason):
        if len(skipped) < 200:
            skipped.append({"path": path if safe_path(path) else "", "reason": reason})
        else:
            raise ValueError("coverage_notice_limit")

    def walk_error(error):
        notice("", "unreadable_directory")

    for parent, directories, names in os.walk(root, followlinks=False, onerror=walk_error):
        entries += len(directories) + len(names)
        if entries > LIMITS["entries"]:
            notice("", "entry_limit")
            break
        directories[:] = sorted(name for name in directories if name not in EXCLUDED)
        for name in list(directories):
            path = Path(parent) / name
            relative = path.relative_to(root).as_posix()
            if path.is_symlink() or not safe_path(relative):
                directories.remove(name)
                notice(relative, "symlink_or_invalid_directory")
        stop = False
        for name in sorted(names):
            if not name.endswith((".py", ".pyi")):
                continue
            path = Path(parent) / name
            relative = path.relative_to(root).as_posix()
            if not safe_path(relative):
                notice("", "invalid_path")
                continue
            if len(files) >= LIMITS["files"]:
                notice("", "file_limit")
                stop = True
                break
            try:
                metadata = path.lstat()
                if not stat.S_ISREG(metadata.st_mode):
                    notice(relative, "symlink_or_special_file")
                    continue
                if metadata.st_size > LIMITS["file_bytes"]:
                    notice(relative, "file_size_limit")
                    continue
                with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW), "rb") as stream:
                    source = stream.read(LIMITS["file_bytes"] + 1)
                if len(source) > LIMITS["file_bytes"]:
                    notice(relative, "file_size_limit")
                    continue
                if total + len(source) > LIMITS["source_bytes"]:
                    notice("", "source_byte_limit")
                    stop = True
                    break
                encoding, _ = tokenize.detect_encoding(io.BytesIO(source).readline)
                text = source.decode(encoding)
                # Preserve the trailing EOF line used by Ruff syntax diagnostics.
                lines = text.split("\n")
                output = destination / relative
                output.parent.mkdir(parents=True, exist_ok=True)
                # Ruff reads UTF-8 snapshots, with no repository configuration nearby.
                output.write_text(text, encoding="utf-8")
                sources[relative] = lines
                total += len(source)
                files.append({"path": relative, "lines": len(lines), "bytes": len(source)})
            except (OSError, UnicodeError, SyntaxError, ValueError):
                notice(relative, "unreadable_or_invalid_encoding")
        if stop:
            break
    return files, skipped, sources, total


def analyze(root):
    report = {
        "schema_version": 1,
        "status": "inconclusive",
        "tool_version": VERSION,
        "files": [],
        "findings": [],
        "skipped_files": [],
        "errors": [],
        "source_bytes": 0,
    }
    try:
        code, version, error = bounded_command(["/usr/local/bin/ruff", "--version"])
        if code or error or version.decode().strip() != f"ruff {VERSION}":
            raise ValueError("ruff_version_mismatch")
        with tempfile.TemporaryDirectory(prefix="codehound-static-", dir="/tmp") as temporary:
            destination = Path(temporary)
            files, skipped, sources, total = scan(root, destination)
            report.update(files=files, skipped_files=skipped, source_bytes=total)
            if files:
                code, output, error = bounded_command(
                    [
                        "/usr/local/bin/ruff",
                        *FLAGS,
                        "--",
                        *[str(destination / file["path"]) for file in files],
                    ]
                )
                if code not in (0, 1) or error:
                    raise ValueError("ruff_execution_error")
                raw = json.loads(output)
                if not isinstance(raw, list) or len(raw) > LIMITS["findings"]:
                    raise ValueError("finding_limit_or_invalid_report")
                if (code == 0) != (not raw):
                    raise ValueError("ruff_exit_report_mismatch")
                for item in raw:
                    path = Path(item["filename"]).relative_to(destination).as_posix()
                    if path not in sources:
                        raise ValueError("invalid_finding_path")
                    start, end = item["location"], item["end_location"]
                    for location in (start, end):
                        row, column = location["row"], location["column"]
                        if (
                            type(row) is not int
                            or type(column) is not int
                            or not 1 <= row <= len(sources[path])
                            or not 1 <= column <= len(sources[path][row - 1]) + 1
                        ):
                            raise ValueError("invalid_finding_location")
                    if (end["row"], end["column"]) < (start["row"], start["column"]):
                        raise ValueError("invalid_finding_range")
                    snippet = " ".join(sources[path][start["row"] - 1].split())
                    if not snippet and start["row"] > 1:
                        snippet = " ".join(sources[path][start["row"] - 2].split())
                    report["findings"].append(
                        {
                            "path": path,
                            "rule": (
                                "invalid-syntax"
                                if item["code"] is None
                                and item["message"].startswith("SyntaxError:")
                                else item["code"]
                            ),
                            "message": item["message"],
                            "line": start["row"],
                            "column": start["column"],
                            "end_line": end["row"],
                            "end_column": end["column"],
                            "snippet": snippet[:300],
                            "source_sha256": hashlib.sha256(snippet.encode()).hexdigest(),
                        }
                    )
            report["status"] = "inconclusive" if skipped else "completed"
    except (
        OSError,
        ValueError,
        TypeError,
        KeyError,
        AttributeError,
        subprocess.TimeoutExpired,
    ) as exc:
        reasons = {
            "ruff_timeout",
            "ruff_output_limit",
            "ruff_version_mismatch",
            "ruff_execution_error",
            "finding_limit_or_invalid_report",
            "ruff_exit_report_mismatch",
            "invalid_finding_path",
            "invalid_finding_location",
            "invalid_finding_range",
            "coverage_notice_limit",
        }
        report["errors"].append(
            str(exc)
            if type(exc) is ValueError and str(exc) in reasons
            else "Ruff inspection could not produce complete valid evidence."
        )
        report["status"] = "inconclusive"
    if len(json.dumps(report).encode()) > LIMITS["report_bytes"]:
        report.update(
            status="inconclusive",
            findings=[],
            files=[],
            source_bytes=0,
            errors=["report_byte_limit"],
        )
    return report


def main():
    payload = json.dumps(analyze(Path("/workspace")), separators=(",", ":")).encode()
    print(PREFIX + sys.argv[1] + ":" + base64.b64encode(payload).decode())


if __name__ == "__main__":
    main()
