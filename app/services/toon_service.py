"""TOON formatter utilities.

This module provides a focused TOON encoder for JSON-like payloads used by the
chat SQL preview path.
"""

from __future__ import annotations

import math
import re
from datetime import date, datetime
from typing import Any, Dict, Iterable, List


JsonPrimitive = str | int | float | bool | None
JsonValue = JsonPrimitive | Dict[str, "JsonValue"] | List["JsonValue"]


class ToonService:
    DEFAULT_DELIMITER = ","
    _UNQUOTED_KEY_RE = re.compile(r"^[A-Za-z_][\w.]*$")
    _NUMERIC_LIKE_RE = re.compile(r"^-?\d+(?:\.\d+)?(?:e[+-]?\d+)?$", re.IGNORECASE)
    _LEADING_ZERO_RE = re.compile(r"^0\d+$")
    _TOKEN_ENCODER = None
    _TOKEN_ENCODER_READY = False

    @classmethod
    def encode(cls, value: Any, indent: int = 2, delimiter: str = DEFAULT_DELIMITER) -> str:
        normalized = cls._normalize(value)
        lines = list(cls._encode_value(normalized, depth=0, indent=max(1, int(indent or 2)), delimiter=delimiter))
        return "\n".join(lines)

    @classmethod
    def estimate_tokens(cls, text: str) -> int:
        content = str(text or "")
        if not content:
            return 0

        if not cls._TOKEN_ENCODER_READY:
            cls._TOKEN_ENCODER_READY = True
            try:
                import tiktoken  # type: ignore

                cls._TOKEN_ENCODER = tiktoken.get_encoding("cl100k_base")
            except Exception:
                cls._TOKEN_ENCODER = None

        encoder = cls._TOKEN_ENCODER
        if encoder is not None:
            try:
                return int(len(encoder.encode(content)))
            except Exception:
                pass

        # Lightweight fallback when tiktoken is unavailable.
        return int(len(re.findall(r"\w+|[^\w\s]", content)))

    @classmethod
    def _normalize(cls, value: Any) -> JsonValue:
        if value is None:
            return None
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            if isinstance(value, float) and not math.isfinite(value):
                return None
            return value
        if isinstance(value, str):
            return value
        if isinstance(value, (datetime, date)):
            return value.isoformat(sep=" ")
        if isinstance(value, dict):
            out: Dict[str, JsonValue] = {}
            for k, v in value.items():
                out[str(k)] = cls._normalize(v)
            return out
        if isinstance(value, (list, tuple, set)):
            return [cls._normalize(v) for v in value]
        return str(value)

    @classmethod
    def _encode_value(
        cls,
        value: JsonValue,
        depth: int,
        indent: int,
        delimiter: str,
    ) -> Iterable[str]:
        if cls._is_primitive(value):
            yield cls._encode_primitive(value, delimiter=delimiter)
            return

        if isinstance(value, list):
            yield from cls._encode_array(None, value, depth, indent, delimiter)
            return

        if isinstance(value, dict):
            for key, item in value.items():
                yield from cls._encode_key_value(key, item, depth, indent, delimiter)

    @staticmethod
    def _is_primitive(value: JsonValue) -> bool:
        return value is None or isinstance(value, (bool, int, float, str))

    @classmethod
    def _encode_key_value(
        cls,
        key: str,
        value: JsonValue,
        depth: int,
        indent: int,
        delimiter: str,
    ) -> Iterable[str]:
        encoded_key = cls._encode_key(key)
        if cls._is_primitive(value):
            yield cls._line(depth, f"{encoded_key}: {cls._encode_primitive(value, delimiter=delimiter)}", indent)
            return
        if isinstance(value, list):
            yield from cls._encode_array(key, value, depth, indent, delimiter)
            return
        if isinstance(value, dict):
            yield cls._line(depth, f"{encoded_key}:", indent)
            if value:
                for child_key, child_value in value.items():
                    yield from cls._encode_key_value(child_key, child_value, depth + 1, indent, delimiter)

    @classmethod
    def _encode_array(
        cls,
        key: str | None,
        values: List[JsonValue],
        depth: int,
        indent: int,
        delimiter: str,
    ) -> Iterable[str]:
        # Empty array
        if not values:
            yield cls._line(depth, cls._format_header(0, key=key, delimiter=delimiter), indent)
            return

        # Primitive array: inline
        if all(cls._is_primitive(v) for v in values):
            primitives = [cls._encode_primitive(v, delimiter=delimiter) for v in values]
            head = cls._format_header(len(values), key=key, delimiter=delimiter)
            yield cls._line(depth, f"{head} {delimiter.join(primitives)}", indent)
            return

        # Tabular array of uniform objects with primitive fields
        if cls._is_tabular_array(values):
            rows = [v for v in values if isinstance(v, dict)]
            fields = list(rows[0].keys()) if rows else []
            head = cls._format_header(len(rows), key=key, fields=fields, delimiter=delimiter)
            yield cls._line(depth, head, indent)
            for row in rows:
                cols = [cls._encode_primitive(row.get(f), delimiter=delimiter) for f in fields]
                yield cls._line(depth + 1, delimiter.join(cols), indent)
            return

        # Fallback mixed array
        head = cls._format_header(len(values), key=key, delimiter=delimiter)
        yield cls._line(depth, head, indent)
        for item in values:
            if cls._is_primitive(item):
                yield cls._line(depth + 1, f"- {cls._encode_primitive(item, delimiter=delimiter)}", indent)
            elif isinstance(item, dict):
                if not item:
                    yield cls._line(depth + 1, "-", indent)
                    continue
                first = True
                for child_key, child_value in item.items():
                    encoded_key = cls._encode_key(child_key)
                    if first and cls._is_primitive(child_value):
                        encoded_value = cls._encode_primitive(child_value, delimiter=delimiter)
                        yield cls._line(depth + 1, f"- {encoded_key}: {encoded_value}", indent)
                    elif first:
                        yield cls._line(depth + 1, f"- {encoded_key}:", indent)
                        if isinstance(child_value, list):
                            yield from cls._encode_array(None, child_value, depth + 3, indent, delimiter)
                        else:
                            for line in cls._encode_value(child_value, depth + 3, indent, delimiter):
                                yield line
                    else:
                        if cls._is_primitive(child_value):
                            encoded_value = cls._encode_primitive(child_value, delimiter=delimiter)
                            yield cls._line(depth + 2, f"{encoded_key}: {encoded_value}", indent)
                        elif isinstance(child_value, list):
                            yield from cls._encode_array(child_key, child_value, depth + 2, indent, delimiter)
                        else:
                            yield cls._line(depth + 2, f"{encoded_key}:", indent)
                            for line in cls._encode_value(child_value, depth + 3, indent, delimiter):
                                yield line
                    first = False
            elif isinstance(item, list):
                # Nested arrays as list items
                nested = cls.encode(item, indent=indent, delimiter=delimiter).splitlines()
                if nested:
                    first_line, *rest = nested
                    yield cls._line(depth + 1, f"- {first_line}", indent)
                    for line in rest:
                        yield cls._line(depth + 2, line, indent)

    @classmethod
    def _is_tabular_array(cls, values: List[JsonValue]) -> bool:
        if not values:
            return False
        if not all(isinstance(v, dict) for v in values):
            return False

        rows = [v for v in values if isinstance(v, dict)]
        first_fields = list(rows[0].keys())
        if not first_fields:
            return False

        first_set = set(first_fields)
        for row in rows:
            if set(row.keys()) != first_set:
                return False
            if any(not cls._is_primitive(row.get(f)) for f in first_fields):
                return False
        return True

    @classmethod
    def _format_header(
        cls,
        length: int,
        key: str | None = None,
        fields: List[str] | None = None,
        delimiter: str = DEFAULT_DELIMITER,
    ) -> str:
        key_part = f"{cls._encode_key(key)}" if key is not None else ""
        delimiter_part = delimiter if delimiter != cls.DEFAULT_DELIMITER else ""
        out = f"{key_part}[{length}{delimiter_part}]"
        if fields is not None:
            out += "{" + delimiter.join(cls._encode_key(f) for f in fields) + "}"
        out += ":"
        return out

    @classmethod
    def _encode_key(cls, key: str | None) -> str:
        text = str(key or "")
        if cls._UNQUOTED_KEY_RE.fullmatch(text):
            return text
        return f"\"{cls._escape_string(text)}\""

    @classmethod
    def _encode_primitive(cls, value: JsonValue, delimiter: str = DEFAULT_DELIMITER) -> str:
        if value is None:
            return "null"
        if isinstance(value, bool):
            return "true" if value else "false"
        if isinstance(value, (int, float)):
            return str(value)
        text = str(value)
        if cls._is_safe_unquoted_string(text, delimiter=delimiter):
            return text
        return f"\"{cls._escape_string(text)}\""

    @classmethod
    def _is_safe_unquoted_string(cls, value: str, delimiter: str = DEFAULT_DELIMITER) -> bool:
        if not value:
            return False
        if value != value.strip():
            return False
        lowered = value.lower()
        if lowered in {"true", "false", "null"}:
            return False
        if cls._NUMERIC_LIKE_RE.fullmatch(value) or cls._LEADING_ZERO_RE.fullmatch(value):
            return False
        if ":" in value:
            return False
        if "\"" in value or "\\" in value:
            return False
        if any(ch in value for ch in "[]{}"):
            return False
        if any(ch in value for ch in "\n\r\t"):
            return False
        if delimiter in value:
            return False
        if value.startswith("-"):
            return False
        return True

    @staticmethod
    def _escape_string(value: str) -> str:
        return (
            value.replace("\\", "\\\\")
            .replace("\"", "\\\"")
            .replace("\n", "\\n")
            .replace("\r", "\\r")
            .replace("\t", "\\t")
        )

    @staticmethod
    def _line(depth: int, content: str, indent: int) -> str:
        return (" " * (depth * indent)) + content
