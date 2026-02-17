from __future__ import annotations

from datetime import datetime
from typing import Any, Awaitable, Callable, Dict, List

from sqlalchemy import text

from app.config import get_settings
from app.assistant.services.manifest_catalog import ManifestCatalog

settings = get_settings()

ResolverFn = Callable[[Dict[str, Any], Dict[str, Any], Dict[str, Any], int, str], List[Dict[str, str]]]
ActionFn = Callable[[Dict[str, Any], Dict[str, Any], Dict[str, Any]], Awaitable[Dict[str, Any]]]


class ManifestFlowPlugin:
    """Generic flow plugin driven by YAML + manifest metadata."""

    def __init__(self, schema, builder, sql_executor):
        self.schema = schema
        self.builder = builder
        self.sql_executor = sql_executor
        self.catalog = ManifestCatalog()

    def resolvers(self) -> Dict[str, ResolverFn]:
        return {
            "generic.lookup": self._resolve_lookup,
        }

    def actions(self) -> Dict[str, ActionFn]:
        return {
            "generic.create_row": self._action_create_row,
        }

    def _resolve_lookup(
        self,
        _: Dict[str, Any],
        state_def: Dict[str, Any],
        session_state: Dict[str, Any],
        page: int,
        search_text: str,
    ) -> List[Dict[str, str]]:
        lookup_cfg = dict(state_def.get("lookup") or {})
        table = str(lookup_cfg.get("table", "")).strip()
        value_column = str(lookup_cfg.get("value_column", "id")).strip() or "id"
        label_columns = [str(x).strip() for x in (lookup_cfg.get("label_columns") or []) if str(x).strip()]
        search_columns = [str(x).strip() for x in (lookup_cfg.get("search_columns") or []) if str(x).strip()]
        page_size = max(1, int(lookup_cfg.get("page_size", 10) or 10))
        order_by = str(lookup_cfg.get("order_by", f"{value_column} DESC")).strip() or f"{value_column} DESC"
        if not table:
            return []

        metadata = dict((session_state.get("flow_context") or {}).get("metadata") or {})
        db_url = (metadata or {}).get("db_connection_string") or settings.DATABASE_URL
        table_columns = self.schema.get_table_columns([table], db_url=db_url).get(table, set())
        if value_column not in table_columns:
            return []

        selected_cols = [value_column] + [c for c in label_columns if c in table_columns and c != value_column]
        selected_cols = list(dict.fromkeys(selected_cols))

        where_parts: List[str] = []
        params: Dict[str, Any] = {
            "limit": page_size,
            "offset": max(0, int(page)) * page_size,
        }

        company_id = (metadata or {}).get("company_id")
        if company_id and "company_id" in table_columns:
            where_parts.append("company_id = :company_id")
            params["company_id"] = company_id

        q = str(search_text or "").strip().lower()
        normalized_q = "".join(ch for ch in q if ch.isalnum())
        if q:
            cols = [value_column] + (search_columns or label_columns)
            search_terms: List[str] = []
            for idx, col in enumerate(cols):
                if col not in table_columns:
                    continue
                key = f"q{idx}"
                key_norm = f"qnorm{idx}"
                if col == value_column and q.isdigit():
                    search_terms.append(f"{col} = :{key}")
                    params[key] = int(q)
                    continue
                search_terms.append(f"LOWER(CAST({col} AS CHAR)) LIKE :{key}")
                params[key] = f"%{q}%"
                if normalized_q:
                    search_terms.append(
                        f"REPLACE(REPLACE(REPLACE(REPLACE(LOWER(CAST({col} AS CHAR)), ' ', ''), '.', ''), ':', ''), '-', '') LIKE :{key_norm}"
                    )
                    params[key_norm] = f"%{normalized_q}%"
            if search_terms:
                where_parts.append("(" + " OR ".join(search_terms) + ")")

        where_clause = f" WHERE {' AND '.join(where_parts)}" if where_parts else ""
        escaped_table = f"`{table}`" if table == "user" else table
        sql = (
            f"SELECT {', '.join(selected_cols)} FROM {escaped_table}{where_clause} "
            f"ORDER BY {order_by} LIMIT :limit OFFSET :offset;"
        )

        engine = self.schema.get_engine_for_url(db_url)
        with engine.connect() as conn:
            rows = [dict(row) for row in conn.execute(text(sql), params).mappings().all()]

        options: List[Dict[str, str]] = []
        for row in rows:
            value = str(row.get(value_column, "")).strip()
            if not value:
                continue
            rendered_labels: List[str] = []
            for col in label_columns:
                raw = row.get(col)
                if raw is None:
                    continue
                shown = str(raw).strip()
                if shown:
                    rendered_labels.append(shown)
            label = " | ".join(rendered_labels) if rendered_labels else value
            options.append({"value": value, "label": label})

        deduped: List[Dict[str, str]] = []
        seen = set()
        for opt in options:
            key = str(opt.get("value", "")).strip()
            if not key or key in seen:
                continue
            seen.add(key)
            deduped.append(opt)
        return deduped

    @staticmethod
    def _eval_condition(expr: str, values: Dict[str, Any]) -> bool:
        if "==" in expr:
            left, right = expr.split("==", 1)
            left = left.strip()
            right = right.strip().strip("'\"")
            if left.startswith("context."):
                key = left.split(".", 1)[1]
                return str(values.get(key, "")).strip().lower() == right.lower()
        if "!=" in expr:
            left, right = expr.split("!=", 1)
            left = left.strip()
            right = right.strip().strip("'\"")
            if left.startswith("context."):
                key = left.split(".", 1)[1]
                return str(values.get(key, "")).strip().lower() != right.lower()
        return False

    @staticmethod
    def _to_scalar(raw: Any) -> Any:
        text_value = str(raw or "").strip()
        if text_value.isdigit():
            return int(text_value)
        return raw

    async def _action_create_row(
        self,
        flow: Dict[str, Any],
        session_state: Dict[str, Any],
        metadata: Dict[str, Any],
    ) -> Dict[str, Any]:
        target_table = str(flow.get("target_table", "")).strip()
        if not target_table:
            return {"status": "error", "message": "Flow target_table is missing."}

        values = dict((session_state.get("flow_context") or {}).get("values") or {})
        field_map = dict(flow.get("field_map") or {})
        if not field_map:
            # Fallback: identity mapping from captured fields.
            field_map = {str(k): str(k) for k in values.keys()}

        required_inputs = [str(x).strip() for x in (flow.get("required_fields") or []) if str(x).strip()]
        missing = [f for f in required_inputs if not str(values.get(f, "")).strip()]

        for cond in (flow.get("required_when") or []):
            if not isinstance(cond, dict):
                continue
            expr = str(cond.get("condition", "")).strip()
            fields = [str(x).strip() for x in (cond.get("fields") or []) if str(x).strip()]
            if expr and self._eval_condition(expr, values):
                missing.extend([f for f in fields if not str(values.get(f, "")).strip()])

        if missing:
            uniq_missing = sorted(set(missing))
            return {"status": "error", "message": "Missing required value(s): " + ", ".join(uniq_missing) + "."}

        fields: Dict[str, Any] = {}
        for capture_key, db_col in field_map.items():
            cap = str(capture_key).strip()
            col = str(db_col).strip()
            if not cap or not col:
                continue
            raw = values.get(cap)
            if str(raw or "").strip() == "":
                continue
            fields[col] = self._to_scalar(raw)

        generated_fields = dict(flow.get("generated_fields") or {})
        for db_col, generator in generated_fields.items():
            col = str(db_col).strip()
            gen = str(generator).strip().lower()
            if not col or col in fields:
                continue
            if gen in {"auto_ref", "__auto_ref__"}:
                fields[col] = f"AUTO-{datetime.utcnow().strftime('%Y%m%d%H%M%S')}"
            elif gen in {"now_utc", "__now_utc__"}:
                fields[col] = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")

        company_id = (metadata or {}).get("company_id")
        actor_user_id = (metadata or {}).get("user_id") or (metadata or {}).get("userId")
        sql, err = self.builder.build_insert(target_table, fields, company_id, actor_user_id=actor_user_id)
        if err:
            return {"status": "error", "message": err}

        result = await self.sql_executor.run({"sql_query": sql, "metadata": metadata})
        if result.get("error"):
            return {"status": "error", "message": str(result.get("error"))}

        row_count = int(result.get("row_count") or 0)
        return {
            "status": "ok",
            "message": f"Create successful. Rows affected: {row_count}.",
            "sql_data": {
                "ran": True,
                "cached": False,
                "query": sql,
                "row_count": row_count,
                "rows_preview": result.get("rows_preview") or [],
            },
        }
