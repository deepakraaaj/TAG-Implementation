"""Typed domain configuration models for runtime validation."""

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator


class _DomainModel(BaseModel):
    model_config = ConfigDict(extra="allow")


class SQLStatementGuardPattern(_DomainModel):
    start: str = Field(min_length=1)
    required: str = Field(min_length=1)


class NameMatchingConfig(_DomainModel):
    substring_min_length: int = Field(ge=1, le=50)
    prefix_min_length: int = Field(ge=1, le=50)
    meaningful_token_min_length: int = Field(ge=1, le=20)
    ratio_threshold: float = Field(ge=0.0, le=1.0)
    max_length_delta: int = Field(ge=0, le=100)
    contains_score: float = Field(ge=0.0, le=1.0)
    prefix_score: float = Field(ge=0.0, le=1.0)


class SQLBuilderHeuristicsConfig(_DomainModel):
    llm_skip_short_query_length: int = Field(ge=0, le=500)
    user_suggestion_candidate_pool_limit: int = Field(ge=1, le=500)
    user_suggestion_min_score: float = Field(ge=0.0, le=1.0)
    unfiltered_select_limit: int = Field(ge=1, le=10000)
    name_matching: NameMatchingConfig


class SQLBuilderPatternsConfig(_DomainModel):
    direct_operation_patterns: List[str] = Field(min_length=1)
    sql_statement_passthrough_pattern: str = Field(min_length=1)
    sql_statement_guard_patterns: List[SQLStatementGuardPattern] = Field(min_length=1)
    forced_table_patterns: List[str] = Field(min_length=1)
    pure_filter_query_patterns: List[str] = Field(min_length=1)
    task_for_clause_patterns: List[str] = Field(min_length=1)
    trailing_date_clause_pattern: str = Field(min_length=1)


class SQLBuilderConfig(_DomainModel):
    patterns: SQLBuilderPatternsConfig
    heuristics: SQLBuilderHeuristicsConfig


class EntityBehaviorConfig(_DomainModel):
    primary_table: str = Field(min_length=1)
    intent_mode: str = Field(min_length=1)
    primary_keywords: List[str] = Field(min_length=1)
    primary_filter_keys: List[str] = Field(min_length=1)
    primary_label: str = Field(min_length=1)
    date_filter_keys: List[str] = Field(min_length=1)
    status_filter_key: str = Field(min_length=1)
    priority_filter_key: str = Field(min_length=1)
    date_phrase_map: Dict[str, str] = Field(min_length=1)
    status_phrase_map: Dict[str, str] = Field(min_length=1)
    count_request_patterns: List[str] = Field(min_length=1)
    user_filter_keys: List[str] = Field(min_length=1)
    self_aliases: List[str] = Field(min_length=1)
    all_users_aliases: List[str] = Field(min_length=1)
    default_entity_prompt: str = Field(min_length=1)
    filter_context_prompt: str = Field(min_length=1)
    explicit_list_request_patterns: List[str] = Field(min_length=1)

    @field_validator("intent_mode")
    @classmethod
    def _validate_intent_mode(cls, value: str) -> str:
        candidate = str(value or "").strip().lower()
        if candidate not in {"llm", "heuristic", "auto"}:
            raise ValueError("intent_mode must be one of: llm, heuristic, auto")
        return candidate


class UserLookupConfig(_DomainModel):
    table: str = Field(min_length=1)
    id_column: str = Field(min_length=1)
    first_name_column: str = Field(min_length=1)
    last_name_column: str = Field(min_length=1)
    tenant_column: str = Field(min_length=1)
    metadata_key: str = Field(min_length=1)
    filter_keys: List[str] = Field(min_length=1)
    canonical_filter_key: str = Field(min_length=1)
    id_filter_key: str = Field(min_length=1)
    search_limit: int = Field(ge=1, le=200)
    fallback_limit: int = Field(ge=1, le=200)
    fallback_name: str = Field(min_length=1)


class LocationLookupConfig(_DomainModel):
    table: str = Field(min_length=1)
    name_column: str = Field(min_length=1)
    tenant_column: str = Field(min_length=1)
    metadata_key: str = Field(min_length=1)
    filter_keys: List[str] = Field(min_length=1)
    canonical_filter_key: str = Field(min_length=1)
    id_filter_keys: List[str] = Field(min_length=1)
    search_limit: int = Field(ge=1, le=200)
    fuzzy_scan_limit: int = Field(ge=1, le=1000)
    fallback_limit: int = Field(ge=1, le=200)


class SelectWorkflowConfig(_DomainModel):
    workflow_id: str = Field(min_length=1)
    workflow_ids: List[str] = Field(min_length=1)
    state: str = Field(min_length=1)
    mode: str = Field(min_length=1)
    next_field: str = Field(min_length=1)
    operation: str = Field(min_length=1)


class CompactReasoningConfig(_DomainModel):
    engine_label: str = Field(min_length=1)
    rules: List[str] = Field(min_length=1)
    response_modes: Dict[str, str] = Field(default_factory=dict)


class AssistantPromptConfig(_DomainModel):
    role_description: str = Field(min_length=1)
    template: str = Field(min_length=1)
    suggested_queries: List[str] = Field(default_factory=list)
    compact_reasoning: Optional[CompactReasoningConfig] = None


class CapabilitiesConfig(_DomainModel):
    description: str = Field(default="")
    categorized_examples: Dict[str, List[str]] = Field(default_factory=dict)
    examples: List[str] = Field(default_factory=list)
    tables_description: Dict[str, str] = Field(default_factory=dict)


class DomainReasoningProfileConfig(_DomainModel):
    name: str = Field(min_length=1)
    behavior_summary: str = Field(min_length=1)
    rules: List[str] = Field(min_length=1)
    response_modes: Dict[str, str] = Field(default_factory=dict)
    evidence_sources: List[str] = Field(min_length=1)
    clarification_policy: str = Field(min_length=1)
    abstention_policy: str = Field(min_length=1)


class DomainWorkflowCandidateConfig(_DomainModel):
    workflow_id: str = Field(min_length=1)
    label: str = Field(min_length=1)
    table: str = Field(min_length=1)
    operation: str = Field(min_length=1)
    trigger_phrases: List[str] = Field(default_factory=list)
    required_fields: List[str] = Field(default_factory=list)
    reasoning: str = Field(default="")
    confidence: int = Field(ge=0, le=100)


class DomainKnowledgeConfig(_DomainModel):
    scope: str = Field(min_length=1)
    primary_entities: List[str] = Field(default_factory=list)
    business_terms: Dict[str, str] = Field(default_factory=dict)
    example_queries: List[str] = Field(default_factory=list)
    categorized_examples: Dict[str, List[str]] = Field(default_factory=dict)
    workflows: List[DomainWorkflowCandidateConfig] = Field(default_factory=list)
    reasoning_profile: DomainReasoningProfileConfig


class DomainConfigModel(_DomainModel):
    name: str = Field(min_length=1)
    description: str = Field(min_length=1)
    entity_behavior: EntityBehaviorConfig
    user_lookup: UserLookupConfig
    location_lookup: LocationLookupConfig
    select_workflow: SelectWorkflowConfig
    sql_builder: SQLBuilderConfig
    assistant_prompt: Optional[AssistantPromptConfig] = None
    capabilities: Optional[CapabilitiesConfig] = None
    domain_knowledge: Optional[DomainKnowledgeConfig] = None


class TableManifestConfig(_DomainModel):
    primary_key: str = Field(min_length=1)
    important_columns: Dict[str, Any] = Field(min_length=1)


class DomainManifestModel(_DomainModel):
    tables: Dict[str, TableManifestConfig] = Field(min_length=1)
    query_templates: Dict[str, Any] = Field(default_factory=dict)
    table_resolution_rules: List[Dict[str, Any]] = Field(default_factory=list)


class DomainSpec(_DomainModel):
    config: DomainConfigModel
    manifest: DomainManifestModel

    def config_dict(self) -> Dict[str, Any]:
        return self.config.model_dump(exclude_none=True)

    def manifest_dict(self) -> Dict[str, Any]:
        return self.manifest.model_dump(exclude_none=True)

    def get_config_section(self, section: str) -> Dict[str, Any]:
        key = str(section or "").strip()
        if not key:
            return {}
        payload = self.config_dict().get(key, {})
        return dict(payload) if isinstance(payload, dict) else {}
