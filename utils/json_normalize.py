# CHANGE: YAML generation. New file. Canonical text for the json_columns of a generated YAML, used by the data validator.
"""Canonical text for JSON, JSONB and HSTORE values, so that equal content compares equal across the two databases.

Postgres prints jsonb with sorted keys and spaces (`{"a": 1}`) and hstore as `"a"=>"1"`. Snowflake prints a VARIANT in
its own form. The same content is therefore different text on each side. This module reads any of these forms and
writes one canonical form: every leaf of the value, at any depth, on its own line, as its path, its type and its text.
The lines are sorted, so key order and spacing do not matter. Numbers are written without trailing zeros.

A value that cannot be read is returned unchanged, so a difference shows as a mismatch, not as an error.
Used by the data validator for the json_columns of a generated YAML. A YAML without json_columns is not touched.
"""

import json
import re
from decimal import Decimal

# One hstore pair: "key"=>"value", or key=>value, with an optional bare NULL. Quoted text may hold escaped characters.
_HSTORE_PAIR = re.compile(r'\s*("(?:[^"\\]|\\.)*"|[^\s",=]+)\s*=>\s*("(?:[^"\\]|\\.)*"|[^\s",]+)\s*(,|\Z)')


def _unquote(token: str) -> str:
    """'"a\\"b"' -> 'a"b'. A bare token is returned as it is."""
    return re.sub(r"\\(.)", r"\1", token[1:-1]) if token.startswith('"') else token


def _hstore(text: str) -> dict:
    """The pairs of an hstore text. Raises ValueError when the text is not hstore."""
    pairs, position = {}, 0
    text = text.strip()
    if not text:
        return pairs
    while True:
        match = _HSTORE_PAIR.match(text, position)
        if not match:
            raise ValueError("not hstore text")
        key, raw, separator = match.groups()
        pairs[_unquote(key)] = None if raw.upper() == "NULL" else _unquote(raw)
        if separator == "":
            return pairs
        position = match.end()


def _number(value: Decimal) -> str:
    """Plain text with no trailing zeros: 1.50 and 1.5 are the same, and 100 is not written as 1E+2."""
    return format(value.normalize(), "f")


def _leaves(value, path: list, out: list) -> None:
    """Append (path, [type, text]) for every leaf. Empty objects and arrays are leaves too, so they are not lost."""
    if isinstance(value, dict):
        if not value:
            out.append((path, ["o", "{}"]))
        for key, item in value.items():
            _leaves(item, path + [key], out)
    elif isinstance(value, list):
        if not value:
            out.append((path, ["a", "[]"]))
        for index, item in enumerate(value):
            _leaves(item, path + [index], out)
    elif value is None:
        out.append((path, ["z", "null"]))
    elif isinstance(value, bool):
        out.append((path, ["b", "true" if value else "false"]))
    elif isinstance(value, (Decimal, int)):
        out.append((path, ["n", _number(Decimal(value))]))
    else:
        out.append((path, ["s", str(value)]))


def canonical_text(text):
    """The canonical form of one JSON, JSONB or HSTORE value. Text that cannot be read is returned unchanged."""
    if text is None:
        return None
    text = str(text)
    try:
        value = json.loads(text, parse_float=Decimal)
    except ValueError:
        try:
            value = _hstore(text)
        except ValueError:
            return text
    leaves = []
    _leaves(value, [], leaves)
    return "\n".join(sorted(json.dumps([path, leaf], ensure_ascii=False, separators=(",", ":"))
                            for path, leaf in leaves))


def flatten_columns(frame, aliases):
    """The frame with each named column replaced by its canonical text. A name the frame does not have is skipped."""
    for alias in aliases or []:
        if alias in frame.columns:
            frame[alias] = frame[alias].map(canonical_text)
    return frame
