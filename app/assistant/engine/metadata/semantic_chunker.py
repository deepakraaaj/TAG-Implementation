from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Dict, List


class SemanticBundleChunker:
    def __init__(self, domain_provider):
        self.domain_provider = domain_provider

    def bundle_dir(self) -> Path:
        domain = self.domain_provider()
        return Path(getattr(domain, "domain_path", "") or "") / "semantic_bundle"

    def has_bundle(self) -> bool:
        bundle_dir = self.bundle_dir()
        return bundle_dir.exists() and any(bundle_dir.glob("*.json"))

    def load_bundle(self) -> Dict[str, Dict[str, Any]]:
        bundle_dir = self.bundle_dir()
        payload: Dict[str, Dict[str, Any]] = {}
        for filename in (
            "schema_context.json",
            "business_semantics.json",
            "relationship_map.json",
            "enum_dictionary.json",
            "query_patterns.json",
        ):
            path = bundle_dir / filename
            if not path.exists():
                continue
            try:
                content = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                continue
            payload[filename] = dict(content) if isinstance(content, dict) else {}
        return payload

    def build_chunks(self) -> List[Dict[str, Any]]:
        bundle = self.load_bundle()
        if not bundle:
            return []

        chunks: List[Dict[str, Any]] = []
        chunks.extend(self._schema_chunks(bundle.get("schema_context.json") or {}))
        chunks.extend(self._business_chunks(bundle.get("business_semantics.json") or {}))
        chunks.extend(self._relationship_chunks(bundle.get("relationship_map.json") or {}))
        chunks.extend(self._enum_chunks(bundle.get("enum_dictionary.json") or {}))
        chunks.extend(self._pattern_chunks(bundle.get("query_patterns.json") or {}))
        chunks.extend(self._report_chunks())
        return chunks

    def _schema_chunks(self, payload: Dict[str, Any]) -> List[Dict[str, Any]]:
        chunks: List[Dict[str, Any]] = []
        for table in payload.get("tables") or []:
            table_name = str(table.get("table_name") or "").strip()
            if not table_name:
                continue
            columns = [
                str(item.get("column_name") or "").strip()
                for item in (table.get("important_columns") or [])
                if str(item.get("column_name") or "").strip()
            ]
            text_parts = [
                f"table {table_name}",
                f"label {str(table.get('label') or '').strip()}",
                f"description {str(table.get('description') or '').strip()}",
                f"columns {'; '.join(columns)}" if columns else "",
                (
                    f"tenant scope {'; '.join(table.get('tenant_scope_candidates') or [])}"
                    if table.get("tenant_scope_candidates")
                    else ""
                ),
                (
                    f"status columns {'; '.join(table.get('status_columns') or [])}"
                    if table.get("status_columns")
                    else ""
                ),
            ]
            chunks.append(
                self._chunk(
                    kind="table",
                    artifact_id=table_name,
                    text=" | ".join(part for part in text_parts if part),
                    candidate_tables=[table_name],
                    route="SQL",
                    source_file="schema_context.json",
                )
            )
        return chunks

    def _business_chunks(self, payload: Dict[str, Any]) -> List[Dict[str, Any]]:
        chunks: List[Dict[str, Any]] = []
        for term in payload.get("glossary") or []:
            term_name = str(term.get("term") or "").strip()
            meaning = str(term.get("meaning") or "").strip()
            if not term_name or not meaning:
                continue
            candidate_tables = [
                str(item).strip()
                for item in (term.get("related_tables") or [])
                if str(item).strip()
            ]
            text = f"business term {term_name} means {meaning}"
            chunks.append(
                self._chunk(
                    kind="term",
                    artifact_id=term_name,
                    text=text,
                    candidate_tables=candidate_tables,
                    route="SQL",
                    source_file="business_semantics.json",
                )
            )

        for entity in payload.get("canonical_entities") or []:
            name = str(entity.get("entity_name") or "").strip()
            description = str(entity.get("description") or "").strip()
            candidate_tables = [
                str(item).strip()
                for item in (entity.get("mapped_source_tables") or [])
                if str(item).strip()
            ]
            if not name:
                continue
            text_parts = [
                f"entity {name}",
                f"description {description}" if description else "",
                f"tables {'; '.join(candidate_tables)}" if candidate_tables else "",
            ]
            chunks.append(
                self._chunk(
                    kind="knowledge",
                    artifact_id=name,
                    text=" | ".join(part for part in text_parts if part),
                    candidate_tables=candidate_tables,
                    route="SQL",
                    source_file="business_semantics.json",
                )
            )
        return chunks

    def _relationship_chunks(self, payload: Dict[str, Any]) -> List[Dict[str, Any]]:
        chunks: List[Dict[str, Any]] = []
        for item in payload.get("relationships") or []:
            join = str(item.get("join") or "").strip()
            table_name = str(item.get("table") or "").strip()
            related_table = str(item.get("related_table") or "").strip()
            if not join:
                continue
            candidate_tables = [value for value in [table_name, related_table] if value]
            chunks.append(
                self._chunk(
                    kind="relationship",
                    artifact_id=self._stable_id("relationship", join),
                    text=f"relationship {join}",
                    candidate_tables=candidate_tables,
                    route="SQL",
                    source_file="relationship_map.json",
                )
            )
        return chunks

    def _enum_chunks(self, payload: Dict[str, Any]) -> List[Dict[str, Any]]:
        chunks: List[Dict[str, Any]] = []
        for item in payload.get("entries") or []:
            table_name = str(item.get("table_name") or "").strip()
            column_name = str(item.get("column_name") or "").strip()
            if not table_name or not column_name:
                continue
            values = [str(value).strip() for value in (item.get("enum_values") or []) if str(value).strip()]
            samples = [str(value).strip() for value in (item.get("sample_values") or []) if str(value).strip()]
            meaning = str(item.get("business_meaning") or "").strip()
            text_parts = [
                f"enum {table_name}.{column_name}",
                f"meaning {meaning}" if meaning else "",
                f"values {'; '.join(values)}" if values else "",
                f"samples {'; '.join(samples)}" if samples else "",
            ]
            chunks.append(
                self._chunk(
                    kind="enum",
                    artifact_id=f"{table_name}.{column_name}",
                    text=" | ".join(part for part in text_parts if part),
                    candidate_tables=[table_name],
                    route="SQL",
                    source_file="enum_dictionary.json",
                )
            )
        return chunks

    def _pattern_chunks(self, payload: Dict[str, Any]) -> List[Dict[str, Any]]:
        chunks: List[Dict[str, Any]] = []
        for index, item in enumerate(payload.get("patterns") or []):
            intent = str(item.get("intent") or "").strip() or f"pattern_{index}"
            examples = [str(value).strip() for value in (item.get("question_examples") or []) if str(value).strip()]
            candidate_tables = [
                str(value).strip()
                for value in (item.get("preferred_tables") or [])
                if str(value).strip()
            ]
            joins = [str(value).strip() for value in (item.get("required_joins") or []) if str(value).strip()]
            filters = [str(value).strip() for value in (item.get("safe_filters") or []) if str(value).strip()]
            text_parts = [
                f"query pattern {intent}",
                f"examples {'; '.join(examples)}" if examples else "",
                f"tables {'; '.join(candidate_tables)}" if candidate_tables else "",
                f"joins {'; '.join(joins)}" if joins else "",
                f"filters {'; '.join(filters)}" if filters else "",
            ]
            chunks.append(
                self._chunk(
                    kind="special_query",
                    artifact_id=intent,
                    text=" | ".join(part for part in text_parts if part),
                    candidate_tables=candidate_tables,
                    route="SQL",
                    source_file="query_patterns.json",
                    sql=str(item.get("optional_sql_template") or "").strip(),
                )
            )

        for index, item in enumerate(payload.get("learned_queries") or []):
            question = str(item.get("question") or "").strip()
            sql = str(item.get("sql") or "").strip()
            if not question or not sql:
                continue
            candidate_tables = [
                str(value).strip()
                for value in (item.get("candidate_tables") or [])
                if str(value).strip()
            ]
            text = f"successful nl2sql example question {question} | sql {sql}"
            chunks.append(
                self._chunk(
                    kind="learned_query",
                    artifact_id=f"learned_query_{index}",
                    text=text,
                    candidate_tables=candidate_tables,
                    route="SQL",
                    source_file="query_patterns.json",
                    sql=sql,
                )
            )
        return chunks

    def _report_chunks(self) -> List[Dict[str, Any]]:
        domain = self.domain_provider()
        domain_path = Path(getattr(domain, "domain_path", "") or "")
        reports_path = domain_path / "reports.json"
        if not reports_path.exists():
            return []
        try:
            payload = json.loads(reports_path.read_text(encoding="utf-8"))
        except Exception:
            return []
        reports = payload.get("reports") if isinstance(payload, dict) else {}
        chunks: List[Dict[str, Any]] = []
        if not isinstance(reports, dict):
            return chunks
        for report_id, item in reports.items():
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or "").strip()
            aliases = [str(alias).strip() for alias in (item.get("aliases") or []) if str(alias).strip()]
            description = str(item.get("description") or "").strip()
            text_parts = [
                f"report {str(report_id).strip()}",
                f"name {name}" if name else "",
                f"aliases {'; '.join(aliases)}" if aliases else "",
                f"description {description}" if description else "",
            ]
            chunks.append(
                self._chunk(
                    kind="report",
                    artifact_id=str(report_id).strip(),
                    text=" | ".join(part for part in text_parts if part),
                    candidate_tables=[],
                    route="REPORT",
                    source_file="reports.json",
                )
            )
        return chunks

    def _chunk(
        self,
        *,
        kind: str,
        artifact_id: str,
        text: str,
        candidate_tables: List[str],
        route: str,
        source_file: str,
        sql: str = "",
    ) -> Dict[str, Any]:
        return {
            "id": self._stable_id(kind, artifact_id, text),
            "kind": kind,
            "artifact_id": artifact_id,
            "text": text,
            "candidate_tables": candidate_tables,
            "route": route,
            "source_file": source_file,
            "sql": sql,
        }

    @staticmethod
    def _stable_id(*parts: str) -> str:
        joined = "::".join(str(part or "").strip() for part in parts)
        return hashlib.sha1(joined.encode("utf-8")).hexdigest()

