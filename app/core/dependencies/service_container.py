from __future__ import annotations

import os
from typing import Any, Optional, Set

from langchain_openai import ChatOpenAI
import redis.asyncio as redis

from app.assistant.nodes.core.chat_node import ChatNode
from app.assistant.nodes.core.intent_node import IntentNode
from app.assistant.nodes.reporting.report_node import ReportNode
from app.assistant.nodes.core.response_node import ResponseNode
from app.assistant.nodes.core.router_node import RouterNode
from app.assistant.nodes.sql.sql_builder_node import SQLBuilderNode
from app.assistant.nodes.sql.sql_execute_node import SQLExecuteNode
from app.assistant.nodes.sql.sql_validate_node import SQLValidateNode
from app.assistant.orchestration.graph import create_graph
from app.assistant.engine.flow.flow_engine import FlowEngine
from app.assistant.engine.flow.plugins.manifest_flow_plugin import ManifestFlowPlugin
from app.assistant.engine.flow.flow_registry import FlowRegistry
from app.assistant.engine.intent.intent_detection_service import IntentDetectionService
from app.assistant.engine.intent.intent_service import IntentService
from app.assistant.engine.metadata.manifest_catalog import ManifestCatalog
from app.assistant.engine.safety.prompt_injection_detector import PromptInjectionDetector
from app.assistant.engine.reporting.reporting_service import ReportingService
from app.assistant.engine.response.response_intelligence import ResponseIntelligence
from app.assistant.engine.router.router_service import RouterService
from app.assistant.engine.sql.sql_builder_service import SQLBuilderService
from app.config import get_settings
from app.domains.registry import DomainRegistry
from app.services.observability.audit_service import AuditService
from app.services.platform.cache import cache
from app.services.platform.cache_service import CacheService
from app.services.chat import ChatHistoryStore, ChatService
from app.services.observability.metrics_service import MetricsService
from app.services.data.schema_service import SchemaService
from app.services.data.sql_validator import SQLValidatorService
from app.services.core.toon_service import ToonService
from app.services.data.user_service import UserService

try:
    from app.services.db_service import DBService
except ImportError:
    DBService = None  # type: ignore[assignment]


class ServiceContainer:
    """Composition root for concrete service wiring."""

    def __init__(self) -> None:
        self.settings = get_settings()
        self._workflow: Optional[Any] = None

        self.domain_provider = DomainRegistry.get_current_domain

        # Shared infrastructure/singletons.
        self.cache = cache
        self.metrics_service = MetricsService()
        self.schema_service = SchemaService()
        self.toon_service = ToonService()
        self.manifest_catalog = ManifestCatalog(domain_provider=self.domain_provider)
        self.prompt_injection_detector = PromptInjectionDetector()

        # LLM clients (centralized construction).
        self.chat_llm = self._new_llm(temperature=0.4)
        self.intent_llm = self._new_llm(temperature=0.0)
        self.router_llm = self._new_llm(temperature=0.0)
        self.intent_detection_llm = self._new_llm(temperature=0.1)
        self.sql_builder_llm = self._new_llm(temperature=0.0)
        self.response_intelligence_llm = self._new_llm(temperature=0.3)

        # Core services.
        self.response_intelligence = ResponseIntelligence(
            domain_provider=self.domain_provider,
            llm=self.response_intelligence_llm,
        )
        self.intent_service = IntentService(llm=self.intent_llm)
        self.router_service = RouterService(
            llm=self.router_llm,
            manifest_catalog=self.manifest_catalog,
            domain_provider=self.domain_provider,
        )
        self.intent_detection_service = IntentDetectionService(
            llm=self.intent_detection_llm,
            domain_provider=self.domain_provider,
            toon_service=self.toon_service,
        )
        self.sql_builder_service = SQLBuilderService(
            llm=self.sql_builder_llm,
            manifest_catalog=self.manifest_catalog,
            domain_provider=self.domain_provider,
            toon_service=self.toon_service,
        )

        self.sql_execute_node = SQLExecuteNode(
            schema_service=self.schema_service,
            domain_provider=self.domain_provider,
        )
        self.flow_engine = FlowEngine(
            registry=FlowRegistry(),
            schema_service=self.schema_service,
            sql_builder_service=self.sql_builder_service,
            sql_executor=self.sql_execute_node,
            plugins=[
                ManifestFlowPlugin(
                    self.schema_service,
                    self.sql_builder_service,
                    self.sql_execute_node,
                    self.manifest_catalog,
                )
            ],
        )

        self.chat_history_store = ChatHistoryStore(
            cache_backend=self.cache,
            ttl_seconds=86400,
            max_messages=100,
        )
        self.chat_service = ChatService(
            schema_service=self.schema_service,
            intent_service=self.intent_service,
            flow_engine=self.flow_engine,
            history_store=self.chat_history_store,
            metrics_service=self.metrics_service,
            toon_service=self.toon_service,
            cache_backend=self.cache,
            workflow_provider=self.get_workflow,
            kv_parser=SQLBuilderService.parse_kv_pairs,
        )
        self.user_service = UserService(
            schema_service=self.schema_service,
            domain_provider=self.domain_provider,
        )

        # Graph nodes.
        self.router_node = RouterNode(router_service=self.router_service)
        self.chat_node = ChatNode(
            llm=self.chat_llm,
            intelligence=self.response_intelligence,
            injection_detector=self.prompt_injection_detector,
        )
        self.intent_node = IntentNode(intent_service=self.intent_service)
        self.sql_builder_node = SQLBuilderNode(
            sql_builder=self.sql_builder_service,
            intent_detector=self.intent_detection_service,
            schema=self.schema_service,
            domain_provider=self.domain_provider,
            kv_parser=SQLBuilderService.parse_kv_pairs,
        )
        self.sql_validate_node = SQLValidateNode(
            validator=SQLValidatorService(allowed_tables=None),
            schema_service=self.schema_service,
            metrics_service=self.metrics_service,
            allowed_mutation_roles=self._allowed_mutation_roles(),
            require_explicit_mutation_permission=bool(
                getattr(self.settings, "MUTATION_REQUIRE_EXPLICIT_PERMISSION", True)
            ),
        )
        self.response_node = ResponseNode()

        # Report stack (optional if DB service is unavailable).
        self._db_service = DBService() if DBService is not None else None
        self._report_node: Optional[ReportNode] = None

    def _new_llm(self, temperature: float) -> ChatOpenAI:
        model_name = os.getenv("LLM_MODEL", self.settings.LLM_MODEL)
        return ChatOpenAI(
            api_key=self.settings.LLM_API_KEY,
            base_url=self.settings.LLM_BASE_URL,
            model=model_name,
            temperature=temperature,
            timeout=self.settings.LLM_TIMEOUT,
            max_retries=self.settings.LLM_MAX_RETRIES,
        )

    def _allowed_mutation_roles(self) -> Set[str]:
        raw_roles = str(getattr(self.settings, "MUTATION_ALLOWED_ROLES", "admin,superadmin"))
        return {str(role).strip().lower() for role in raw_roles.split(",") if str(role).strip()}

    async def startup(self) -> None:
        await self.cache.connect()
        self._workflow = create_graph(
            router_node=self.router_node,
            chat_node=self.chat_node,
            intent_node=self.intent_node,
            sql_builder_node=self.sql_builder_node,
            sql_validate_node=self.sql_validate_node,
            sql_execute_node=self.sql_execute_node,
            response_node=self.response_node,
        )

    async def shutdown(self) -> None:
        await self.cache.close()
        self._workflow = None

    def get_workflow(self) -> Optional[Any]:
        return self._workflow

    def get_report_node(self) -> ReportNode:
        if self._report_node is None:
            if self._db_service is None:
                raise RuntimeError("DBService is not available")
            self._report_node = ReportNode(
                reporting_service=ReportingService(domain_provider=self.domain_provider),
                db_service=self._db_service,
                audit_service=AuditService(db_service=self._db_service),
                cache_service=CacheService(
                    enabled=bool(getattr(self.settings, "CACHE_ENABLED", True)),
                    default_ttl=int(getattr(self.settings, "CACHE_TTL_SECONDS", 3600) or 3600),
                    redis_url=str(getattr(self.settings, "REDIS_URL", "")),
                    redis_client_factory=lambda url: redis.from_url(
                        url,
                        encoding="utf-8",
                        decode_responses=True,
                    ),
                ),
                metrics_service=self.metrics_service,
            )
        return self._report_node


_container: Optional[ServiceContainer] = None


def get_container() -> ServiceContainer:
    global _container
    if _container is None:
        _container = ServiceContainer()
    return _container
