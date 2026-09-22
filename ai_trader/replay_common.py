"""Canonical replay files and safe, exclusive output creation. Standard library only."""
from __future__ import annotations
import hashlib
import json
import math
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parent


def canonical(value):
    return (json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False,
                       allow_nan=False) + '\n').encode('utf-8')


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def sha256(value):
    if type(value) is not str or re.fullmatch('[a-f0-9]{64}', value) is None:
        raise ValueError('INVALID_SHA256')
    return value


def finite(value, minimum=0):
    if type(value) not in (int, float) or not math.isfinite(value) or value < minimum:
        raise ValueError('INVALID_FINITE_NUMBER')
    return value


def integer(value, minimum=0):
    if type(value) is not int or value < minimum:
        raise ValueError('INVALID_INTEGER')
    return value


def read_json(path):
    def reject(value):
        raise ValueError('NONFINITE_JSON')
    def unique(pairs):
        out = {}
        for key, value in pairs:
            if key in out:
                raise ValueError('DUPLICATE_JSON_KEY')
            out[key] = value
        return out
    return json.loads(Path(path).read_text(encoding='utf-8-sig'),
                      parse_constant=reject, object_pairs_hook=unique)


def output_path(path):
    path = Path(path).resolve()
    if path.is_relative_to(ROOT / 'data') or '.git' in path.parts:
        raise ValueError('EXISTING_DATA_OR_GIT_OUTPUT_FORBIDDEN')
    return path


def write_new(path, content):
    path = output_path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    # Exclusive creation, including for empty/corrupted existing files.
    with path.open('xb') as handle:
        handle.write(content)
    return path
