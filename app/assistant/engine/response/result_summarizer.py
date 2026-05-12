from __future__ import annotations

from collections import Counter
from typing import Any, Dict, List, Optional

_HIDDEN_EXACT: frozenset[str] = frozenset({"id", "company_id"})
_HIDDEN_SUFFIXES: tuple[str, ...] = ("_id",)
_NULL_STRINGS: frozenset[str] = frozenset({"", "none", "null", "n/a", "-", "na"})
_BOOL_TRUE: frozenset[str] = frozenset({"yes", "true", "1", "enabled", "active"})
_BOOL_FALSE: frozenset[str] = frozenset({"no", "false", "0", "disabled", "inactive"})
_ACRONYMS: frozenset[str] = frozenset({"vts", "gps", "id", "ims", "fms", "pms", "hrms"})


def is_hidden_col(col: str) -> bool:
    k = str(col or "").strip().lower()
    if not k or k in _HIDDEN_EXACT or k.endswith(".id"):
        return True
    return any(k.endswith(s) for s in _HIDDEN_SUFFIXES) or ".company_id" in k


def col_label(col: str) -> str:
    parts = str(col or "").strip().replace(".", "_").split("_")
    return " ".join(p.upper() if p.lower() in _ACRONYMS else p.title() for p in parts if p)


def is_null_value(val: Any) -> bool:
    return val is None or str(val).strip().lower() in _NULL_STRINGS


def coerce_numeric(val: Any) -> Optional[float]:
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


class ResultSummarizer:
    """Deterministic row-set analyzer returning up to 2 insight sentences.

    No LLM involved — cheap, fast, degrades gracefully.
    """

    MIN_ROWS: int = 3
    DOMINANCE_THRESHOLD: float = 0.60

    @classmethod
    def summarize(
        cls,
        rows_preview: List[Dict[str, Any]],
        row_count: int,
        total_records: int,
        sql_query: str = "",
    ) -> str:
        rows = [dict(r) for r in (rows_preview or []) if isinstance(r, dict)]
        total = max(int(total_records or 0), int(row_count or 0), len(rows))

        if not rows or total < cls.MIN_ROWS:
            return ""

        visible_rows = [
            {k: v for k, v in r.items() if not is_hidden_col(k)}
            for r in rows
        ]
        if not visible_rows or not visible_rows[0]:
            return ""

        cols = list(visible_rows[0].keys())
        insights: list[str] = []

        for col in cols:
            if len(insights) >= 2:
                break

            values = [r.get(col) for r in visible_rows]
            non_null = [v for v in values if not is_null_value(v)]
            if not non_null:
                continue

            str_vals = [str(v).strip() for v in non_null]
            unique = list(dict.fromkeys(str_vals))

            # Skip high-cardinality columns (names, addresses, codes, etc.)
            if len(unique) == len(str_vals) and len(str_vals) >= 4:
                continue

            label = col_label(col)

            # Uniform value across all rows
            if len(unique) == 1:
                v = unique[0]
                v_lower = v.lower()
                if v_lower in _BOOL_TRUE:
                    insights.append(f"All {total} are {label}.")
                elif v_lower in _BOOL_FALSE:
                    insights.append(f"None are {label}.")
                else:
                    insights.append(f"All have {label}: {v}.")
                continue

            # Dominant value (≥ 60% of non-null preview rows)
            freq = Counter(str_vals)
            top_val, top_count = freq.most_common(1)[0]
            if len(non_null) >= cls.MIN_ROWS and top_count / len(non_null) >= cls.DOMINANCE_THRESHOLD:
                insights.append(f"Most ({top_count}/{len(non_null)}) have {label}: {top_val}.")
                continue

            # Numeric range
            nums = [coerce_numeric(v) for v in non_null]
            if all(n is not None for n in nums) and len(nums) >= cls.MIN_ROWS:
                lo, hi = min(nums), max(nums)  # type: ignore[type-var]
                if lo != hi:
                    lo_s = str(int(lo)) if lo == int(lo) else str(lo)  # type: ignore[arg-type]
                    hi_s = str(int(hi)) if hi == int(hi) else str(hi)  # type: ignore[arg-type]
                    insights.append(f"{label} ranges from {lo_s} to {hi_s}.")

        return " ".join(insights)
