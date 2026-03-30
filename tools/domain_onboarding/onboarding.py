from __future__ import annotations

import copy
import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Literal, Optional

from app.services.data.schema_service import SchemaService
from tools.domain_onboarding.generator import DomainGenerationArtifacts, DomainGenerationService


TableCategory = Literal["core", "supporting", "bridge", "noise"]


def _compact(value: Any) -> Any:
    if isinstance(value, dict):
        out: Dict[str, Any] = {}
        for key, item in value.items():
            compacted = _compact(item)
            if compacted in (None, "", [], {}):
                continue
            out[str(key)] = compacted
        return out
    if isinstance(value, list):
        items = [_compact(item) for item in value]
        return [item for item in items if item not in (None, "", [], {})]
    return value


def _normalize_tokens(identifier: str) -> List[str]:
    text = str(identifier or "").strip().replace("_", " ")
    return [token for token in text.lower().split() if token]


def _dedupe_keep_order(values: Iterable[str]) -> List[str]:
    out: List[str] = []
    seen: set[str] = set()
    for value in values:
        cleaned = str(value or "").strip()
        normalized = cleaned.lower()
        if not cleaned or normalized in seen:
            continue
        seen.add(normalized)
        out.append(cleaned)
    return out


@dataclass
class TableAssessment:
    table_name: str
    category: TableCategory
    include: bool
    confidence: int
    reasons: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return _compact(asdict(self))


@dataclass
class ClarificationQuestion:
    id: str
    key: str
    question: str
    recommended_answer: str
    confidence: int
    context: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return _compact(asdict(self))


@dataclass
class DomainOnboardingAnalysis:
    domain_name: str
    database_target: str
    connection_source: str
    password_redacted: bool
    table_assessments: List[TableAssessment] = field(default_factory=list)
    included_tables: List[str] = field(default_factory=list)
    excluded_tables: List[str] = field(default_factory=list)
    clarification_questions: List[ClarificationQuestion] = field(default_factory=list)
    artifacts: Optional[DomainGenerationArtifacts] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "domain_name": self.domain_name,
            "database": {
                "target": self.database_target,
                "source": self.connection_source,
                "password_redacted": self.password_redacted,
            },
            "table_assessments": [item.to_dict() for item in self.table_assessments],
            "included_tables": list(self.included_tables),
            "excluded_tables": list(self.excluded_tables),
            "clarification_questions": [item.to_dict() for item in self.clarification_questions],
            "artifacts": {
                "review_report": self.artifacts.review_report if self.artifacts is not None else {},
            },
        }


class DomainOnboardingService:
    _NOISE_TERMS = {
        "audit",
        "history",
        "log",
        "logs",
        "migration",
        "migrations",
        "schema",
        "cache",
        "session",
        "token",
        "temp",
        "tmp",
        "backup",
        "archive",
    }
    _BRIDGE_TERMS = {
        "mapping",
        "bridge",
        "junction",
        "association",
        "link",
        "xref",
        "relation",
        "relations",
    }

    def __init__(self, generator: Optional[DomainGenerationService] = None) -> None:
        self.generator = generator or DomainGenerationService()

    @staticmethod
    def _table_name(table: Dict[str, Any]) -> str:
        return str(table.get("name") or "").strip()

    @classmethod
    def _column_names(cls, table: Dict[str, Any]) -> List[str]:
        return [
            str(column.get("name") or "").strip()
            for column in (table.get("columns") or [])
            if str(column.get("name") or "").strip()
        ]

    @classmethod
    def _descriptive_columns(cls, table: Dict[str, Any]) -> List[str]:
        descriptive: List[str] = []
        for column in cls._column_names(table):
            lowered = column.lower()
            if lowered == "id" or lowered.endswith("_id"):
                continue
            if lowered in {
                "company_id",
                "tenant_id",
                "created_at",
                "updated_at",
                "date_created",
                "date_updated",
                "created_by",
                "updated_by",
                "is_active",
            }:
                continue
            descriptive.append(column)
        return descriptive

    @classmethod
    def _classify_table(cls, table: Dict[str, Any]) -> TableAssessment:
        table_name = cls._table_name(table)
        tokens = set(_normalize_tokens(table_name))
        foreign_keys = table.get("foreign_keys") or []
        foreign_key_count = len(foreign_keys)
        columns = cls._column_names(table)
        descriptive_columns = cls._descriptive_columns(table)

        reasons: List[str] = []
        category: TableCategory = "supporting"
        include = True
        confidence = 65

        if tokens & cls._NOISE_TERMS:
            reasons.append("table name matches technical/system patterns")
            category = "noise"
            include = False
            confidence = 92
        elif tokens & cls._BRIDGE_TERMS or (foreign_key_count >= 2 and len(descriptive_columns) <= 2 and len(columns) <= 8):
            reasons.append("table looks like a bridge or mapping table")
            category = "bridge"
            confidence = 84
        else:
            core_score = 0
            support_score = 0
            if DomainGenerationService._status_column(table):
                core_score += 3
                reasons.append("has a status-like column")
            if DomainGenerationService._date_columns(table):
                core_score += 2
                reasons.append("has date/time columns")
            if DomainGenerationService._priority_column(table):
                core_score += 1
                reasons.append("has a priority-like column")
            if foreign_key_count:
                core_score += 1
            if set(_normalize_tokens(table_name)) & (
                DomainGenerationService._PRIMARY_TABLE_HINTS
                | DomainGenerationService._USER_TABLE_TERMS
                | DomainGenerationService._LOCATION_TABLE_TERMS
            ):
                if tokens & DomainGenerationService._PRIMARY_TABLE_HINTS:
                    core_score += 4
                    reasons.append("table name looks like an operational record")
                if tokens & DomainGenerationService._USER_TABLE_TERMS:
                    support_score += 4
                    reasons.append("table name looks like a people/user lookup")
                if tokens & DomainGenerationService._LOCATION_TABLE_TERMS:
                    support_score += 4
                    reasons.append("table name looks like a facility/location lookup")
            if DomainGenerationService._best_display_column(table):
                support_score += 1
            if len(descriptive_columns) <= 3 and foreign_key_count == 0:
                support_score += 2
                reasons.append("table looks like a small lookup/reference table")

            if core_score >= max(support_score + 2, 4):
                category = "core"
                confidence = 88 if core_score >= 6 else 76
            else:
                category = "supporting"
                confidence = 78 if support_score >= 4 else 66
                if not reasons:
                    reasons.append("table is likely supportive but not obviously operational")

        if not reasons:
            reasons.append("classification based on fallback schema heuristics")
        return TableAssessment(
            table_name=table_name,
            category=category,
            include=include,
            confidence=confidence,
            reasons=_dedupe_keep_order(reasons),
        )

    @staticmethod
    def _merge_metadata_hints(
        metadata_hints: Optional[Dict[str, Any]],
        *,
        primary_table: str = "",
        user_table: str = "",
        location_table: str = "",
    ) -> Dict[str, Any]:
        hints = copy.deepcopy(dict(metadata_hints or {}))
        if str(primary_table or "").strip():
            hints["primary_table"] = str(primary_table).strip()
        if str(user_table or "").strip():
            hints["user_table"] = str(user_table).strip()
        if str(location_table or "").strip():
            hints["location_table"] = str(location_table).strip()
        return hints

    @staticmethod
    def _question_from_review_item(item: Dict[str, Any]) -> ClarificationQuestion:
        key = str(item.get("key") or "").strip()
        reason = str(item.get("reason") or "").strip()
        confidence = int(item.get("confidence") or 0)
        inferred_value = item.get("inferred_value")
        inferred_text = json.dumps(inferred_value) if isinstance(inferred_value, (dict, list)) else str(inferred_value or "").strip()

        question = f"Please review `{key}`."
        recommended_answer = inferred_text or "review"
        if key == "entity_behavior.primary_table":
            question = f"Should `{inferred_text}` be treated as the main business table for this domain?"
            recommended_answer = inferred_text or "confirm primary table"
        elif key == "user_lookup.table":
            question = f"Should `{inferred_text}` be treated as the people or user table?"
            recommended_answer = inferred_text or "confirm user table"
        elif key == "location_lookup.table":
            question = f"Should `{inferred_text}` be treated as the facility or location table?"
            recommended_answer = inferred_text or "confirm location table"
        elif key == "domain_knowledge.workflows":
            question = "Do the inferred workflow candidates match your real business actions, or should they be replaced?"
            recommended_answer = "review workflows"
        elif key.endswith("status_filter_key"):
            question = f"Which column actually represents status on the main records? Current guess: `{inferred_text}`."
            recommended_answer = inferred_text or "provide status column"
        elif key.endswith("priority_filter_key"):
            question = f"Which column actually represents priority? Current guess: `{inferred_text}`."
            recommended_answer = inferred_text or "provide priority column"
        elif key.endswith("date_filter_keys"):
            question = f"Which date column should the assistant use by default? Current guess: `{inferred_text}`."
            recommended_answer = inferred_text or "provide date column"

        question_id = re.sub(r"[^a-z0-9]+", "_", key.lower()).strip("_") or "review_item"
        return ClarificationQuestion(
            id=question_id,
            key=key,
            question=question,
            recommended_answer=recommended_answer,
            confidence=confidence,
            context=reason,
        )

    @classmethod
    def _clarification_questions(
        cls,
        assessments: List[TableAssessment],
        review_report: Dict[str, Any],
    ) -> List[ClarificationQuestion]:
        questions: List[ClarificationQuestion] = []
        excluded_noise = [item.table_name for item in assessments if not item.include]
        if excluded_noise:
            preview = ", ".join(excluded_noise[:6])
            if len(excluded_noise) > 6:
                preview += ", ..."
            questions.append(
                ClarificationQuestion(
                    id="ignore_noise_tables",
                    key="exclude_tables",
                    question=f"Ignore likely system or noise tables from onboarding: {preview}?",
                    recommended_answer="yes",
                    confidence=90,
                    context="These tables matched audit/log/migration/system heuristics and were excluded by default.",
                )
            )

        for item in review_report.get("needs_review") or []:
            if not isinstance(item, dict):
                continue
            questions.append(cls._question_from_review_item(item))
        return questions[:8]

    def analyze_snapshot(
        self,
        *,
        domain_name: str,
        schema_snapshot: Dict[str, Any],
        description: str = "",
        metadata_hints: Optional[Dict[str, Any]] = None,
        include_tables: Optional[List[str]] = None,
        exclude_tables: Optional[List[str]] = None,
        primary_table: str = "",
        user_table: str = "",
        location_table: str = "",
        connection_source: str = "snapshot",
        database_target: str = "",
    ) -> DomainOnboardingAnalysis:
        tables = [dict(table) for table in (schema_snapshot.get("tables") or []) if isinstance(table, dict)]
        assessments = [self._classify_table(table) for table in tables]
        default_included = {item.table_name for item in assessments if item.include}
        explicit_included = {str(name or "").strip() for name in (include_tables or []) if str(name or "").strip()}
        explicit_excluded = {str(name or "").strip() for name in (exclude_tables or []) if str(name or "").strip()}

        included_tables = sorted((default_included | explicit_included) - explicit_excluded)
        excluded_tables = sorted({item.table_name for item in assessments if item.table_name not in included_tables})
        filtered_snapshot = {
            "database_target": str(database_target or schema_snapshot.get("database_target") or ""),
            "table_count": len(included_tables),
            "tables": [
                dict(table)
                for table in tables
                if self._table_name(table) in set(included_tables)
            ],
        }
        if not filtered_snapshot["tables"]:
            raise ValueError("No tables remain after onboarding filtering")

        effective_hints = self._merge_metadata_hints(
            metadata_hints,
            primary_table=primary_table,
            user_table=user_table,
            location_table=location_table,
        )
        artifacts = self.generator.build_artifacts(
            domain_name=domain_name,
            schema_snapshot=filtered_snapshot,
            description=description,
            metadata_hints=effective_hints,
        )
        questions = self._clarification_questions(assessments, artifacts.review_report)
        return DomainOnboardingAnalysis(
            domain_name=str(domain_name or "").strip(),
            database_target=str(filtered_snapshot["database_target"] or ""),
            connection_source=str(connection_source or "snapshot"),
            password_redacted=True,
            table_assessments=assessments,
            included_tables=included_tables,
            excluded_tables=excluded_tables,
            clarification_questions=questions,
            artifacts=artifacts,
        )

    def analyze(
        self,
        *,
        domain_name: str,
        db_url: Optional[str] = None,
        description: str = "",
        metadata_hints: Optional[Dict[str, Any]] = None,
        include_tables: Optional[List[str]] = None,
        exclude_tables: Optional[List[str]] = None,
        primary_table: str = "",
        user_table: str = "",
        location_table: str = "",
    ) -> DomainOnboardingAnalysis:
        snapshot = self.generator.introspect_schema(db_url=db_url)
        safe_target = SchemaService._safe_db_target(
            str(db_url or snapshot.get("database_target") or "").strip()
        )
        return self.analyze_snapshot(
            domain_name=domain_name,
            schema_snapshot=snapshot,
            description=description,
            metadata_hints=metadata_hints,
            include_tables=include_tables,
            exclude_tables=exclude_tables,
            primary_table=primary_table,
            user_table=user_table,
            location_table=location_table,
            connection_source="provided_db_url" if str(db_url or "").strip() else "settings.DATABASE_URL",
            database_target=safe_target,
        )

    def write_analysis_report(self, analysis: DomainOnboardingAnalysis, path: Path) -> Path:
        payload = analysis.to_dict()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        return path

    def write_domain(
        self,
        analysis: DomainOnboardingAnalysis,
        *,
        output_root: Path,
        force: bool = False,
    ) -> DomainGenerationArtifacts:
        if analysis.artifacts is None:
            raise ValueError("Onboarding analysis does not contain generated artifacts")
        return self.generator.write_artifacts(analysis.artifacts, output_root=output_root, force=force)
