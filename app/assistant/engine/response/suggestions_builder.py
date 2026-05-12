from __future__ import annotations

import re
from collections import Counter
from typing import Any, Dict, List, Optional

from app.assistant.engine.response.result_summarizer import (
    _BOOL_FALSE,
    _BOOL_TRUE,
    col_label,
    is_hidden_col,
    is_null_value,
)


def _source_table(sql: str) -> str:
    m = re.search(r"\bFROM\s+([A-Za-z_][A-Za-z0-9_]*)\b", str(sql or ""), re.IGNORECASE)
    return m.group(1).lower().strip() if m else ""


class SuggestionsBuilder:
    """Builds 2–3 contextual follow-up chip suggestions from query results + domain config.

    All ID columns are excluded automatically.
    """

    MAX_SUGGESTIONS: int = 3

    @classmethod
    def build(
        cls,
        rows_preview: List[Dict[str, Any]],
        row_count: int,
        sql_query: str = "",
        crud_entities: Optional[List[str]] = None,
        entity_display_names: Optional[Dict[str, Any]] = None,
    ) -> List[str]:
        suggestions: list[str] = []
        rows = [dict(r) for r in (rows_preview or []) if isinstance(r, dict)]
        total = int(row_count or len(rows))
        table = _source_table(sql_query)
        crud = [str(e or "").strip().lower() for e in (crud_entities or [])]
        display = dict(entity_display_names or {})

        if not rows:
            if table and table in crud:
                suggestions.append(f"Add new {cls._entity_label(table, display)}")
            return suggestions

        visible_rows = [
            {k: v for k, v in r.items() if not is_hidden_col(k)}
            for r in rows
        ]
        cols = list(visible_rows[0].keys()) if visible_rows else []

        # 1) Filter-by: columns with 2–6 distinct values, not high-cardinality free-text
        for col in cols:
            if len(suggestions) >= cls.MAX_SUGGESTIONS - 1:
                break
            values = [r.get(col) for r in visible_rows]
            non_null = [str(v).strip() for v in values if not is_null_value(v)]
            unique = list(dict.fromkeys(non_null))
            # Skip if all values are unique (names, codes, addresses)
            if len(unique) == len(non_null) and len(non_null) >= 4:
                continue
            if 2 <= len(unique) <= 6:
                suggestions.append(f"Filter by {col_label(col)}")

        # 2) Show-minority: if one value dominates ≥ 75% and exactly 2 distinct values
        for col in cols:
            if len(suggestions) >= cls.MAX_SUGGESTIONS - 1:
                break
            values = [r.get(col) for r in visible_rows]
            non_null = [str(v).strip() for v in values if not is_null_value(v)]
            if len(non_null) < 3:
                continue
            freq = Counter(non_null)
            if len(freq) != 2:
                continue
            top_val, top_count = freq.most_common(1)[0]
            if top_count / len(non_null) < 0.75:
                continue
            minority_val = [v for v in freq if v != top_val][0]
            entity = cls._entity_label(table, display) if table else "records"
            if minority_val.lower() in _BOOL_FALSE:
                suggestions.append(f"Show non-{col_label(col)} {entity}s")
            elif minority_val.lower() in _BOOL_TRUE:
                suggestions.append(f"Show {col_label(col)} {entity}s only")
            else:
                suggestions.append(f"Show {minority_val} {entity}s")

        # 3) CRUD shortcut
        if total > 0 and table and table in crud:
            label = cls._entity_label(table, display)
            cta = f"Add new {label}"
            if cta not in suggestions:
                suggestions.append(cta)

        return suggestions[: cls.MAX_SUGGESTIONS]

    @staticmethod
    def _entity_label(table: str, display: Dict[str, Any]) -> str:
        entry = display.get(table, {})
        if isinstance(entry, dict):
            return str(entry.get("label", "") or table).strip()
        return str(table).replace("_", " ").title()
