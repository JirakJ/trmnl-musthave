"""Minimální zápis TOML (stdlib má jen čtení) pro config.local.toml spravovaný webovou správou.

Podporuje to, co konfigurace používá: řetězce, čísla, bool, data, pole skalárů, tabulky, vnořené tabulky,
pole tabulek ([[countdown]], [[layout.profile]]) a slovníky uvnitř pole tabulek jako inline tabulky.
Výstup se vždy ověří zpětným načtením (`dumps` vyhodí ValueError, když by se nevrátil stejný obsah).
"""

from __future__ import annotations

import math
import re
import tomllib
from datetime import date, datetime, time

_BARE = re.compile(r"^[A-Za-z0-9_-]+$")


def _key(key: str) -> str:
    return key if _BARE.match(key) else _string(key)


def _string(text: str) -> str:
    out = ['"']
    for ch in text:
        if ch == '"':
            out.append('\\"')
        elif ch == "\\":
            out.append("\\\\")
        elif ch == "\n":
            out.append("\\n")
        elif ch == "\t":
            out.append("\\t")
        elif ord(ch) < 0x20 or ord(ch) == 0x7F:
            out.append(f"\\u{ord(ch):04x}")
        else:
            out.append(ch)
    out.append('"')
    return "".join(out)


def _value(value) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("non-finite float")
        return repr(value)
    if isinstance(value, str):
        return _string(value)
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    if isinstance(value, (list, tuple)):
        return "[" + ", ".join(_value(v) for v in value) + "]"
    if isinstance(value, dict):
        return "{ " + ", ".join(f"{_key(k)} = {_value(v)}" for k, v in value.items()) + " }"
    raise ValueError(f"unsupported TOML value {type(value).__name__}")


def _is_table_array(value) -> bool:
    return isinstance(value, list) and bool(value) and all(isinstance(v, dict) for v in value)


def _emit(table: dict, prefix: str, lines: list[str]) -> None:
    scalars = {k: v for k, v in table.items() if not isinstance(v, dict) and not _is_table_array(v)}
    for k, v in scalars.items():
        lines.append(f"{_key(k)} = {_value(v)}")
    for k, v in table.items():
        name = f"{prefix}.{_key(k)}" if prefix else _key(k)
        if isinstance(v, dict):
            lines.append("")
            lines.append(f"[{name}]")
            _emit(v, name, lines)
    for k, v in table.items():
        name = f"{prefix}.{_key(k)}" if prefix else _key(k)
        if _is_table_array(v):
            for item in v:
                lines.append("")
                lines.append(f"[[{name}]]")
                for ik, iv in item.items():
                    lines.append(f"{_key(ik)} = {_value(iv)}")


def dumps(data: dict, header: str = "") -> str:
    lines: list[str] = [f"# {line}" if line else "#" for line in header.splitlines()]
    _emit(data, "", lines)
    text = "\n".join(lines).strip("\n") + "\n"
    if tomllib.loads(text) != _normalize(data):
        raise ValueError("TOML round-trip mismatch")
    return text


def _normalize(value):
    if isinstance(value, dict):
        return {k: _normalize(v) for k, v in value.items()}
    if isinstance(value, tuple):
        return [_normalize(v) for v in value]
    if isinstance(value, list):
        return [_normalize(v) for v in value]
    return value
