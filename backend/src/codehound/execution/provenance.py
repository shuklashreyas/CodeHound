"""Bind loaded trusted controllers to their source; this is not host attestation."""

import hashlib
import json
import os
import stat
import sys
from dataclasses import dataclass
from functools import wraps
from pathlib import Path

MAX_SOURCE_BYTES = 512 * 1024
_ROOT = Path(__file__).absolute().parents[1]
_BINDINGS = {}


class EvaluatorChanged(RuntimeError):
    def __init__(self):
        super().__init__(
            "Trusted evaluator sources changed or are unavailable. Restart the worker."
        )


def read_trusted_source(path):
    """Read a bounded regular operator-owned file, without following a leaf symlink."""
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(descriptor, "rb") as source:
            info = os.fstat(source.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_SOURCE_BYTES:
                raise EvaluatorChanged()
            raw = source.read(MAX_SOURCE_BYTES + 1)
        if len(raw) > MAX_SOURCE_BYTES:
            raise EvaluatorChanged()
        return raw
    except (OSError, ValueError):
        raise EvaluatorChanged() from None


@dataclass(frozen=True)
class SourceBinding:
    path: Path
    label: str
    sha256: str

    def ensure_current(self):
        if hashlib.sha256(read_trusted_source(self.path)).hexdigest() != self.sha256:
            raise EvaluatorChanged()


def bind_source(path):
    """Call from module scope: reject stale bytecode before labeling its source."""
    frame = sys._getframe(1)
    try:
        path = Path(path).absolute()
        label = path.relative_to(_ROOT).as_posix()
        if frame.f_code.co_name != "<module>":
            raise EvaluatorChanged()
        raw = read_trusted_source(path)
        compiled = compile(
            raw,
            frame.f_code.co_filename,
            "exec",
            dont_inherit=True,
            optimize=sys.flags.optimize,
        )
        if compiled != frame.f_code:
            raise EvaluatorChanged()
        binding = SourceBinding(path, label, hashlib.sha256(raw).hexdigest())
        previous = _BINDINGS.get(label)
        if previous is not None and previous != binding:
            raise EvaluatorChanged()
        _BINDINGS[label] = binding
        return binding
    except (OSError, ValueError, SyntaxError, UnicodeError):
        raise EvaluatorChanged() from None
    finally:
        del frame


@dataclass(frozen=True)
class ControllerBinding:
    sources: tuple[SourceBinding, ...]

    def ensure_current(self):
        for source in self.sources:
            source.ensure_current()

    def to_dict(self):
        identity = {
            "schema_version": 1,
            "sources": {source.label: source.sha256 for source in self.sources},
            "python": {
                "implementation": sys.implementation.name,
                "version": ".".join(str(value) for value in sys.version_info[:3]),
                "cache_tag": sys.implementation.cache_tag,
            },
        }
        raw = json.dumps(identity, sort_keys=True, separators=(",", ":")).encode()
        return identity | {"sha256": hashlib.sha256(raw).hexdigest()}


def controller_binding(labels=None):
    """Freeze the source bindings of explicitly registered imported controllers."""
    keys = sorted(_BINDINGS if labels is None else set(labels) | {"execution/provenance.py"})
    try:
        binding = ControllerBinding(tuple(_BINDINGS[key] for key in keys))
    except KeyError:
        raise EvaluatorChanged() from None
    binding.ensure_current()
    return binding


def guard_evaluator(*labels):
    """Check fixed controller sources around a stage, without masking cancellation."""

    def decorate(operation):
        @wraps(operation)
        async def guarded(*args, **kwargs):
            binding = controller_binding(labels)
            result = await operation(*args, **kwargs)
            binding.ensure_current()
            if isinstance(result, dict):
                result["controller_binding"] = binding.to_dict()
            return result

        return guarded

    return decorate


_SOURCE_BINDING = bind_source(__file__)
