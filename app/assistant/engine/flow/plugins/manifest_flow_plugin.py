from __future__ import annotations

from datetime import datetime
import re
from typing import Any, Awaitable, Callable, Dict, List

from sqlalchemy import text

from app.config import get_settings

settings = get_settings()

ResolverFn = Callable[[Dict[str, Any], Dict[str, Any], Dict[str, Any], int, str], List[Dict[str, str]]]
ActionFn = Callable[[Dict[str, Any], Dict[str, Any], Dict[str, Any]], Awaitable[Dict[str, Any]]]


class ManifestFlowPlugin:
    """Generic flow plugin driven by YAML + manifest metadata."""

    def __init__(self, schema, builder, sql_executor, manifest_catalog=None):
        if manifest_catalog is None:
            from app.assistant.engine.metadata.manifest_catalog import ManifestCatalog
            from app.domains.registry import DomainRegistry

            manifest_catalog = ManifestCatalog(domain_provider=DomainRegistry.get_current_domain)
        self.schema = schema
        self.builder = builder
        self.sql_executor = sql_executor
        self.catalog = manifest_catalog

    def resolvers(self) -> Dict[str, ResolverFn]:
        return {
            "generic.lookup": self._resolve_lookup,
        }

    def actions(self) -> Dict[str, ActionFn]:
        return {
            "generic.create_row": self._action_create_row,
        }

    @staticmethod
    def _dedupe_keep_order(values: List[str]) -> List[str]:
        deduped: List[str] = []
        seen = set()
        for value in values:
            cleaned = str(value or "").strip().lower()
            if not cleaned or cleaned in seen:
                continue
            seen.add(cleaned)
            deduped.append(cleaned)
        return deduped

    @staticmethod
    def _normalize_tokens(raw: str) -> List[str]:
        text_value = str(raw or "").strip().lower()
        if not text_value:
            return []
        return [tok for tok in re.split(r"[^a-z0-9]+", text_value) if tok]

    @staticmethod
    def _token_stem(token: str) -> str:
        value = str(token or "").strip().lower()
        if len(value) <= 4:
            return value
        suffixes = [
            "ments",
            "ment",
            "ations",
            "ation",
            "ingly",
            "ings",
            "ing",
            "ers",
            "ies",
            "es",
            "ed",
            "er",
            "s",
        ]
        for suffix in suffixes:
            if value.endswith(suffix) and len(value) - len(suffix) >= 4:
                if suffix == "ies":
                    return f"{value[:-3]}y"
                return value[: -len(suffix)]
        return value

    @staticmethod
    def _phonetic_token_variants(token: str) -> List[str]:
        value = str(token or "").strip().lower()
        if not value:
            return []

        variants: List[str] = []
        # Common operator spelling variation: "shoban" vs "soban".
        if value.startswith("sh") and len(value) > 3:
            variants.append(f"s{value[2:]}")
        elif value.startswith("s") and not value.startswith("sh") and len(value) > 2:
            variants.append(f"sh{value[1:]}")
        return variants

    @classmethod
    def _token_forms(cls, token: str, token_aliases: Dict[str, List[str]]) -> List[str]:
        base = str(token or "").strip().lower()
        if not base:
            return []
        forms = [base]
        forms.extend(str(item or "").strip().lower() for item in (token_aliases.get(base) or []))
        forms.extend(cls._phonetic_token_variants(base))
        stemmed = cls._token_stem(base)
        if stemmed and stemmed != base:
            forms.append(stemmed)
        return cls._dedupe_keep_order(forms)

    @classmethod
    def _token_form_groups(
        cls,
        raw: str,
        ignore_terms: List[str] | None = None,
        token_aliases: Dict[str, List[str]] | None = None,
        max_tokens: int = 4,
    ) -> List[List[str]]:
        ignore = {str(item).strip().lower() for item in (ignore_terms or []) if str(item).strip()}
        normalized_aliases: Dict[str, List[str]] = {}
        for raw_key, raw_values in dict(token_aliases or {}).items():
            key = str(raw_key or "").strip().lower()
            if not key:
                continue
            if isinstance(raw_values, list):
                normalized_aliases[key] = [
                    str(item).strip().lower()
                    for item in raw_values
                    if str(item).strip()
                ]
            else:
                value = str(raw_values or "").strip().lower()
                normalized_aliases[key] = [value] if value else []

        tokens = cls._normalize_tokens(raw)
        if ignore:
            filtered = [tok for tok in tokens if tok not in ignore]
            if filtered:
                tokens = filtered
        groups: List[List[str]] = []
        for token in tokens[: max(1, int(max_tokens or 4))]:
            forms = cls._token_forms(token, normalized_aliases)
            if forms:
                groups.append(forms)
        return groups

    @classmethod
    def _search_text_variants(
        cls,
        raw: str,
        ignore_terms: List[str] | None = None,
        token_aliases: Dict[str, List[str]] | None = None,
    ) -> List[str]:
        q = str(raw or "").strip().lower()
        if not q:
            return []

        variants: List[str] = [q]
        tokens = [tok for tok in re.split(r"\s+", q) if tok]
        if tokens:
            first = tokens[0]
            if len(first) > 3:
                alt_tokens = list(tokens)
                if first.endswith("s"):
                    alt_tokens[0] = first[:-1]
                else:
                    alt_tokens[0] = f"{first}s"
                variants.append(" ".join(alt_tokens))

        ignore = {str(item).strip().lower() for item in (ignore_terms or []) if str(item).strip()}
        filtered_tokens = cls._normalize_tokens(q)
        if ignore:
            filtered = [tok for tok in filtered_tokens if tok not in ignore]
            if filtered:
                filtered_tokens = filtered
        if filtered_tokens:
            variants.append(" ".join(filtered_tokens))
            first = filtered_tokens[0]
            if len(first) > 3:
                alt_filtered = list(filtered_tokens)
                if first.endswith("s"):
                    alt_filtered[0] = first[:-1]
                else:
                    alt_filtered[0] = f"{first}s"
                variants.append(" ".join(alt_filtered))

        groups = cls._token_form_groups(
            q,
            ignore_terms=ignore_terms,
            token_aliases=token_aliases,
            max_tokens=4,
        )
        if groups:
            primary_tokens = [forms[0] for forms in groups if forms]
            if primary_tokens:
                variants.append(" ".join(primary_tokens))
            for idx, forms in enumerate(groups):
                for form in forms[1:]:
                    alt_tokens = [group[0] for group in groups if group]
                    if idx < len(alt_tokens):
                        alt_tokens[idx] = form
                    if alt_tokens:
                        variants.append(" ".join(alt_tokens))

        return cls._dedupe_keep_order(variants)

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

        raw_ignore_terms = lookup_cfg.get("search_ignore_terms")
        ignore_terms = [str(item).strip().lower() for item in (raw_ignore_terms or []) if str(item).strip()]
        raw_token_aliases = lookup_cfg.get("search_token_aliases")
        token_aliases: Dict[str, List[str]] = {}
        if isinstance(raw_token_aliases, dict):
            for raw_key, raw_values in raw_token_aliases.items():
                key = str(raw_key or "").strip().lower()
                if not key:
                    continue
                if isinstance(raw_values, list):
                    token_aliases[key] = [str(item).strip().lower() for item in raw_values if str(item).strip()]
                else:
                    value = str(raw_values or "").strip().lower()
                    token_aliases[key] = [value] if value else []

        query_variants = self._search_text_variants(
            search_text,
            ignore_terms=ignore_terms,
            token_aliases=token_aliases,
        )
        token_groups = self._token_form_groups(
            search_text,
            ignore_terms=ignore_terms,
            token_aliases=token_aliases,
            max_tokens=4,
        )
        if query_variants:
            cols = [value_column] + (search_columns or label_columns)
            search_terms: List[str] = []
            for idx, col in enumerate(cols):
                if col not in table_columns:
                    continue
                for q_idx, q in enumerate(query_variants):
                    key = f"q{idx}_{q_idx}"
                    key_norm = f"qnorm{idx}_{q_idx}"
                    normalized_q = "".join(ch for ch in q if ch.isalnum())
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

                if token_groups:
                    and_terms: List[str] = []
                    for token_idx, forms in enumerate(token_groups):
                        token_or_terms: List[str] = []
                        for form_idx, form in enumerate(forms):
                            if not form:
                                continue
                            token_key = f"qtok{idx}_{token_idx}_{form_idx}"
                            token_key_norm = f"qtoknorm{idx}_{token_idx}_{form_idx}"
                            token_or_terms.append(f"LOWER(CAST({col} AS CHAR)) LIKE :{token_key}")
                            params[token_key] = f"%{form}%"
                            normalized_form = "".join(ch for ch in form if ch.isalnum())
                            if normalized_form:
                                token_or_terms.append(
                                    "REPLACE(REPLACE(REPLACE(REPLACE(LOWER(CAST("
                                    f"{col}"
                                    " AS CHAR)), ' ', ''), '.', ''), ':', ''), '-', '') LIKE :"
                                    f"{token_key_norm}"
                                )
                                params[token_key_norm] = f"%{normalized_form}%"
                        if token_or_terms:
                            and_terms.append("(" + " OR ".join(token_or_terms) + ")")
                    if and_terms:
                        search_terms.append("(" + " AND ".join(and_terms) + ")")
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
                # Suppress write-operation preview tables in the client.
                "rows_preview": [],
            },
        }
