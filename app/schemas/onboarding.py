from __future__ import annotations

from typing import Any, Dict, List, Literal

from pydantic import BaseModel, Field, field_validator


def _dedupe_strings(values: Any) -> List[str]:
    if not isinstance(values, list):
        return []

    cleaned: List[str] = []
    seen: set[str] = set()
    for value in values:
        text = str(value or "").strip()
        normalized = text.lower()
        if not text or normalized in seen:
            continue
        seen.add(normalized)
        cleaned.append(text)
    return cleaned


class SimpleOnboardingRelationship(BaseModel):
    from_table: str
    from_columns: List[str] = Field(default_factory=list)
    to_table: str
    to_columns: List[str] = Field(default_factory=list)


class SimpleOnboardingArtifact(BaseModel):
    categories: Dict[str, List[str]] = Field(default_factory=dict)
    selected_tables: List[str] = Field(default_factory=list)
    table_descriptions: Dict[str, str] = Field(default_factory=dict)
    relationships: List[SimpleOnboardingRelationship] = Field(default_factory=list)
    business_context: str = ""
    metrics: List[Dict[str, Any]] = Field(default_factory=list)


class SimpleOnboardingTable(BaseModel):
    name: str
    category: str
    description: str
    selected: bool = False
    suggested_action: Literal["select", "ignore"]
    selection_reason: str = ""
    business_score: int = 0
    columns: List[str] = Field(default_factory=list)
    related_tables: List[str] = Field(default_factory=list)


class SimpleOnboardingRequest(BaseModel):
    db_url: str | None = None
    business_context: str = ""
    selection_mode: Literal["review", "ai"] = "review"
    selected_tables: List[str] = Field(default_factory=list)
    include_tables: List[str] = Field(default_factory=list)
    exclude_tables: List[str] = Field(default_factory=list)
    include_categories: List[str] = Field(default_factory=list)
    exclude_categories: List[str] = Field(default_factory=list)
    bulk_include_patterns: List[str] = Field(default_factory=list)
    bulk_exclude_patterns: List[str] = Field(default_factory=list)

    @field_validator(
        "selected_tables",
        "include_tables",
        "exclude_tables",
        "include_categories",
        "exclude_categories",
        "bulk_include_patterns",
        "bulk_exclude_patterns",
        mode="before",
    )
    @classmethod
    def _normalize_string_lists(cls, value: Any) -> List[str]:
        return _dedupe_strings(value)

    @field_validator("db_url", "business_context", mode="before")
    @classmethod
    def _strip_optional_text(cls, value: Any) -> str | None:
        if value is None:
            return None
        text = str(value).strip()
        return text


class SimpleOnboardingResponse(BaseModel):
    database_target: str
    total_tables: int
    selection_mode: Literal["review", "ai"]
    categories: Dict[str, List[str]] = Field(default_factory=dict)
    selected_tables: List[str] = Field(default_factory=list)
    ignored_tables: List[str] = Field(default_factory=list)
    tables: List[SimpleOnboardingTable] = Field(default_factory=list)
    artifact: SimpleOnboardingArtifact
