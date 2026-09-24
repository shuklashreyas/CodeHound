"""Strict, bounded JSON at the boundary between candidate code and the controller."""

import json
import math

MAX_JSON_DEPTH = 64


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON key")
        result[key] = value
    return result


def _constant(value):
    raise ValueError("Non-finite JSON number")


def _float(value):
    result = float(value)
    if not math.isfinite(result):
        raise ValueError("Non-finite JSON number")
    return result


def load_evidence(raw: bytes, *, limit: int):
    if len(raw) > limit:
        raise ValueError("JSON evidence exceeds its limit")
    value = json.loads(
        raw,
        object_pairs_hook=_object,
        parse_constant=_constant,
        parse_float=_float,
    )
    stack = [(value, 0)]
    while stack:
        item, depth = stack.pop()
        if depth > MAX_JSON_DEPTH:
            raise ValueError("JSON evidence nesting exceeds its limit")
        if isinstance(item, str):
            # Lone surrogates parse in Python but cannot be served as UTF-8 JSON.
            item.encode("utf-8")
        elif isinstance(item, dict):
            for key in item:
                key.encode("utf-8")
            stack.extend((child, depth + 1) for child in item.values())
        elif isinstance(item, list):
            stack.extend((child, depth + 1) for child in item)
    return value
