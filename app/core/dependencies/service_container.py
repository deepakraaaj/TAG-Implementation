from __future__ import annotations

import logging
import os
from typing import Any, Optional, Set
import urllib.error
import urllib.request
from urllib.parse import urlsplit

from langchain_openai import ChatOpenAI
import redis.asyncio as redis

from app.apps import AppRegistry
from app.assistant.nodes.core.chat_node import ChatNode
from app.assistant.nodes.core.guardrail_node import GuardrailNode
from app.assistant.nodes.core.intermediate_node import IntermediateNode
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
from app.assistant.engine.metadata.domain_semantic_retriever import DomainSemanticRetriever
from app.assistant.engine.metadata.manifest_catalog import ManifestCatalog
from app.assistant.engine.safety.prompt_injection_detector import PromptInjectionDetector
from app.assistant.engine.reporting.reporting_service import ReportingService
from app.assistant.engine.response.response_intelligence import ResponseIntelligence
from app.assistant.engine.router.router_service import RouterService
from app.assistant.engine.sql.sql_builder_service import SQLBuilderService
from app.config import ConfigurationError, get_settings
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
from app.services.guardrails import EvidenceService, IntermediateService, ValidatorService, VerifierService

try:
    from app.services.db_service import DBService
except ImportError:
    DBService = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)


class ServiceContainer:
    """Composition root for concrete service wiring."""

    def __init__(self) -> None:
        self.settings = get_settings()
        self._workflow: Optional[Any] = None

        self.domain_provider = DomainRegistry.get_current_domain
        self.app_registry = AppRegistry.from_settings(self.settings)

        # Shared infrastructure/singletons.
        self.cache = cache
        self.metrics_service = MetricsService()
        self.schema_service = SchemaService()
        self.toon_service = ToonService()
        self.semantic_retriever = DomainSemanticRetriever(
            domain_provider=self.domain_provider,
        )
        self.manifest_catalog = ManifestCatalog(
            domain_provider=self.domain_provider,
            schema_service=self.schema_service,
            semantic_retriever=self.semantic_retriever,
        )
        self.prompt_injection_detector = PromptInjectionDetector()
        self.intermediate_service = IntermediateService(domain_provider=self.domain_provider)
        self.evidence_service = EvidenceService(domain_provider=self.domain_provider)
        self.verifier_service = VerifierService()
        self.validator_service = ValidatorService()

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
            semantic_retriever=self.semantic_retriever,
        )
        self.intent_detection_service = IntentDetectionService(
            llm=self.intent_detection_llm,
            domain_provider=self.domain_provider,
            toon_service=self.toon_service,
            manifest_catalog=self.manifest_catalog,
            semantic_retriever=self.semantic_retriever,
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
            semantic_retriever=self.semantic_retriever,
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
        self.intermediate_node = IntermediateNode(intermediate_service=self.intermediate_service)
        self.chat_node = ChatNode(
            llm=self.chat_llm,
            intelligence=self.response_intelligence,
            injection_detector=self.prompt_injection_detector,
            metrics_service=self.metrics_service,
        )
        self.guardrail_node = GuardrailNode(
            intermediate_service=self.intermediate_service,
            evidence_service=self.evidence_service,
            verifier_service=self.verifier_service,
            validator_service=self.validator_service,
            metrics_service=self.metrics_service,
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
        self._db_service: Optional[Any] = None
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

    @staticmethod
    def _build_check(status_value: str, required: bool, detail: str) -> dict[str, Any]:
        return {
            "status": status_value,
            "required": required,
            "detail": detail,
        }

    def _validate_runtime_config(self) -> None:
        self.settings.validate_runtime()

    def _primary_database_ready(self) -> bool:
        ping = getattr(self.schema_service, "ping", None)
        if not callable(ping):
            return False
        try:
            return bool(ping(self.settings.DATABASE_URL))
        except Exception:
            logger.exception("Primary database readiness check failed")
            return False

    def _report_database_ready(self) -> bool:
        if DBService is None:
            return False
        try:
            db_service = self.get_db_service()
            ping = getattr(db_service, "ping", None)
            if not callable(ping):
                return False
            return bool(ping())
        except Exception:
            logger.exception("Reporting database readiness check failed")
            return False

    @staticmethod
    def _safe_http_target(url: str) -> str:
        try:
            parsed = urlsplit(str(url or ""))
            return f"{parsed.scheme}://{parsed.netloc}{parsed.path}"
        except Exception:
            return "<unknown-url>"

    def _strict_startup_probes_enabled(self) -> bool:
        explicit = getattr(self.settings, "STRICT_STARTUP_PROBES", None)
        if explicit is not None:
            return bool(explicit)
        return str(getattr(self.settings, "APP_ENV", "") or "").strip().lower() == "production"

    def _llm_ready(self) -> Optional[bool]:
        base_url = str(getattr(self.settings, "LLM_BASE_URL", "") or "").strip().rstrip("/")
        if not base_url:
            return None

        timeout = max(0.1, float(getattr(self.settings, "LLM_HEALTHCHECK_TIMEOUT_SECONDS", 5.0) or 5.0))
        api_key = str(getattr(self.settings, "LLM_API_KEY", "") or "").strip()
        headers = {}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"

        probe_urls: list[str] = []
        if not base_url.endswith("/models"):
            probe_urls.append(f"{base_url}/models")
        probe_urls.append(base_url)

        seen: set[str] = set()
        for url in probe_urls:
            if url in seen:
                continue
            seen.add(url)
            request = urllib.request.Request(url, headers=headers, method="GET")
            try:
                with urllib.request.urlopen(request, timeout=timeout) as response:
                    status_code = int(getattr(response, "status", 200) or 200)
                if 200 <= status_code < 500:
                    return True
            except urllib.error.HTTPError as exc:
                if 200 <= int(exc.code) < 500:
                    return True
                logger.warning("LLM endpoint health probe failed for %s: HTTP %s", self._safe_http_target(url), exc.code)
            except Exception as exc:
                logger.warning(
                    "LLM endpoint health probe failed for %s: %s",
                    self._safe_http_target(url),
                    type(exc).__name__,
                )
        return False

    def _safe_database_target(self, db_url: str) -> str:
        safe_target = getattr(self.schema_service, "_safe_db_target", None)
        if callable(safe_target):
            try:
                return str(safe_target(db_url))
            except Exception:
                return "<unknown-db>"
        return "<unknown-db>"

    def app_database_snapshot(self) -> dict[str, Any]:
        app_registry = getattr(self, "app_registry", None)
        if app_registry is None or not callable(getattr(app_registry, "enabled", None)) or not app_registry.enabled():
            return {"enabled": False, "apps": {}}

        apps: dict[str, dict[str, Any]] = {}
        for app_id, app_config in app_registry.list_apps():
            db_url = str(getattr(app_config, "database_url", "") or "").strip()
            reachable = False
            if db_url:
                try:
                    reachable = bool(self.schema_service.ping(db_url))
                except Exception:
                    logger.exception("Application database readiness check failed for app_id=%s", app_id)
            apps[app_id] = {
                "display_name": getattr(app_config, "display_name", app_id),
                "domain_name": getattr(app_config, "domain_name", "") or app_id,
                "target": self._safe_database_target(db_url),
                "reachable": reachable,
            }
        return {"enabled": True, "apps": apps}

    def domain_registry_snapshot(self) -> dict[str, Any]:
        app_registry = getattr(self, "app_registry", None)
        domain_to_apps: dict[str, list[str]] = {}
        if app_registry is not None and callable(getattr(app_registry, "enabled", None)) and app_registry.enabled():
            for app_id, app_config in app_registry.list_apps():
                domain_name = str(getattr(app_config, "domain_name", "") or app_id).strip()
                if not domain_name:
                    continue
                domain_to_apps.setdefault(domain_name, []).append(app_id)
        else:
            default_domain = str(getattr(self.settings, "DOMAIN", "") or "").strip()
            if not default_domain:
                return {"enabled": False, "domains": {}}
            domain_to_apps[default_domain] = []

        domains: dict[str, dict[str, Any]] = {}
        for domain_name, app_ids in sorted(domain_to_apps.items()):
            try:
                with DomainRegistry.use_domain(domain_name):
                    domain = DomainRegistry.get_current_domain()
                    manifest = domain.manifest if isinstance(domain.manifest, dict) else {}
                    tables = manifest.get("tables") if isinstance(manifest.get("tables"), dict) else {}
                    diagnostics = domain.get_config_layer_diagnostics()
                    config_layers = diagnostics.get("config_layers") if isinstance(diagnostics, dict) else []
                    conflicts = diagnostics.get("conflicts") if isinstance(diagnostics, dict) else []
                    domains[domain_name] = {
                        "status": "ok",
                        "app_ids": list(app_ids),
                        "active_domain": domain.name,
                        "primary_table": str(domain.config.get("entity_behavior", {}).get("primary_table", "") or "").strip(),
                        "table_count": len(tables),
                        "used_fallback_domain": bool(diagnostics.get("used_fallback_domain")) if isinstance(diagnostics, dict) else False,
                        "config_layers": [
                            str(layer.get("name") or "").strip()
                            for layer in config_layers
                            if isinstance(layer, dict) and str(layer.get("name") or "").strip()
                        ],
                        "conflict_count": len(conflicts) if isinstance(conflicts, list) else 0,
                        "detail": "Domain loaded successfully",
                    }
            except Exception as exc:
                logger.exception("Domain registry load failed for domain=%s", domain_name)
                domains[domain_name] = {
                    "status": "error",
                    "app_ids": list(app_ids),
                    "detail": f"{type(exc).__name__}: {exc}",
                }

        return {"enabled": True, "domains": domains}

    def _configured_domain_names(self) -> list[str]:
        app_registry = getattr(self, "app_registry", None)
        domain_names: set[str] = set()

        if app_registry is not None and callable(getattr(app_registry, "enabled", None)) and app_registry.enabled():
            for app_id, app_config in app_registry.list_apps():
                domain_name = str(getattr(app_config, "domain_name", "") or app_id).strip()
                if domain_name:
                    domain_names.add(domain_name)
        else:
            default_domain = str(getattr(self.settings, "DOMAIN", "") or "").strip()
            if default_domain:
                domain_names.add(default_domain)

        return sorted(domain_names)

    def _warm_semantic_retrieval(self) -> None:
        retriever = getattr(self, "semantic_retriever", None)
        if retriever is None:
            return

        is_enabled = getattr(retriever, "is_enabled", None)
        if callable(is_enabled) and not bool(is_enabled()):
            return

        warmup = getattr(retriever, "warmup", None)
        reindex = getattr(retriever, "reindex", None)

        for domain_name in self._configured_domain_names():
            try:
                with DomainRegistry.use_domain(domain_name):
                    if callable(warmup):
                        payload = warmup()
                    elif callable(reindex):
                        payload = {"artifacts": 0, "indexed": int(reindex() or 0)}
                    else:
                        payload = {"artifacts": 0, "indexed": 0}
                logger.info(
                    "Semantic retriever warmed domain=%s artifacts=%s indexed=%s",
                    domain_name,
                    int(payload.get("artifacts") or 0),
                    int(payload.get("indexed") or 0),
                )
            except Exception:
                logger.exception("Semantic retriever warmup failed for domain=%s", domain_name)

    async def readiness_snapshot(self) -> dict[str, Any]:
        checks: dict[str, dict[str, Any]] = {
            "container": self._build_check("ok", True, "Service container is initialized"),
        }

        try:
            self._validate_runtime_config()
            checks["config"] = self._build_check("ok", True, "Runtime configuration is valid")
        except ConfigurationError as exc:
            checks["config"] = self._build_check("not_ready", True, str(exc))

        if self._workflow is None:
            checks["workflow"] = self._build_check("not_ready", True, "Workflow graph is not initialized")
        else:
            checks["workflow"] = self._build_check("ok", True, "Workflow graph is ready")

        if checks["config"]["status"] == "ok" and self._primary_database_ready():
            checks["database"] = self._build_check("ok", True, "Primary database is reachable")
        elif checks["config"]["status"] == "ok":
            checks["database"] = self._build_check("not_ready", True, "Primary database is unavailable")
        else:
            checks["database"] = self._build_check("not_ready", True, "Primary database check skipped due to invalid configuration")

        if DBService is None:
            checks["reporting"] = self._build_check("disabled", False, "Report DB service is unavailable in this build")
        elif checks["config"]["status"] == "ok" and self._report_database_ready():
            checks["reporting"] = self._build_check("ok", False, "Reporting database path is reachable")
        elif checks["config"]["status"] == "ok":
            checks["reporting"] = self._build_check("degraded", False, "Reporting database path is unavailable")
        else:
            checks["reporting"] = self._build_check("degraded", False, "Reporting database check skipped due to invalid configuration")

        cache_configured = bool(self.cache and callable(getattr(self.cache, "is_configured", None)) and self.cache.is_configured())
        if not cache_configured:
            checks["cache"] = self._build_check("disabled", False, "Redis cache is not configured")
        else:
            ping_cache = getattr(self.cache, "ping", None)
            cache_ok = False
            if callable(ping_cache):
                try:
                    cache_ok = bool(await ping_cache())
                except Exception:
                    cache_ok = False
            using_fallback = bool(
                callable(getattr(self.cache, "using_fallback", None)) and self.cache.using_fallback()
            )
            if using_fallback:
                checks["cache"] = self._build_check(
                    "degraded",
                    False,
                    "Redis cache is unavailable; using in-memory fallback in this process",
                )
            elif cache_ok:
                checks["cache"] = self._build_check("ok", False, "Redis cache is reachable")
            else:
                checks["cache"] = self._build_check("degraded", False, "Redis cache is unavailable; requests will continue without cache")

        llm_ready = self._llm_ready()
        if llm_ready is None:
            checks["llm"] = self._build_check("disabled", False, "LLM endpoint probe is not configured")
        elif llm_ready:
            checks["llm"] = self._build_check("ok", True, "LLM endpoint is reachable")
        else:
            checks["llm"] = self._build_check("not_ready", True, "LLM endpoint is unavailable")

        app_database_snapshot = self.app_database_snapshot()
        if not app_database_snapshot.get("enabled"):
            checks["tenant_databases"] = self._build_check("disabled", False, "Application registry is disabled")
        else:
            failed_apps = [
                app_id
                for app_id, payload in (app_database_snapshot.get("apps") or {}).items()
                if isinstance(payload, dict) and not bool(payload.get("reachable"))
            ]
            if failed_apps:
                checks["tenant_databases"] = self._build_check(
                    "degraded",
                    False,
                    "Unreachable application databases: " + ", ".join(failed_apps),
                )
            else:
                checks["tenant_databases"] = self._build_check(
                    "ok",
                    False,
                    f"{len(app_database_snapshot.get('apps') or {})} application database(s) reachable",
                )

        domain_snapshot = self.domain_registry_snapshot()
        if not domain_snapshot.get("enabled"):
            checks["domains"] = self._build_check("disabled", False, "Domain registry snapshot is unavailable")
        else:
            failed_domains = [
                domain_name
                for domain_name, payload in (domain_snapshot.get("domains") or {}).items()
                if isinstance(payload, dict) and str(payload.get("status") or "").strip().lower() != "ok"
            ]
            if failed_domains:
                checks["domains"] = self._build_check(
                    "degraded",
                    False,
                    "Domain registry load failures: " + ", ".join(failed_domains),
                )
            else:
                checks["domains"] = self._build_check(
                    "ok",
                    False,
                    f"{len(domain_snapshot.get('domains') or {})} domain(s) loaded",
                )

        required_failures = any(check["required"] and check["status"] != "ok" for check in checks.values())
        degraded = any(not check["required"] and check["status"] == "degraded" for check in checks.values())
        overall_status = "not_ready" if required_failures else ("degraded" if degraded else "ok")

        return {
            "status": overall_status,
            "ready": not required_failures,
            "env": self.settings.APP_ENV,
            "checks": checks,
            "app_databases": app_database_snapshot,
            "domains": domain_snapshot,
        }

    async def startup(self) -> None:
        self._validate_runtime_config()
        if not self._primary_database_ready():
            if self._strict_startup_probes_enabled():
                raise RuntimeError("Primary database is not reachable")
            logger.warning("Primary database is not reachable")
        if DBService is not None and not self._report_database_ready():
            if self._strict_startup_probes_enabled():
                raise RuntimeError("Reporting database path is not reachable")
            logger.warning("Reporting database path is not reachable")
        await self.cache.connect()

        llm_ready = self._llm_ready()
        if llm_ready is False:
            message = "LLM endpoint is not reachable"
            if self._strict_startup_probes_enabled():
                raise RuntimeError(message)
            logger.warning(message)

        app_database_snapshot = self.app_database_snapshot()
        if app_database_snapshot.get("enabled"):
            unavailable_apps = [
                app_id
                for app_id, payload in (app_database_snapshot.get("apps") or {}).items()
                if isinstance(payload, dict) and not bool(payload.get("reachable"))
            ]
            if unavailable_apps:
                logger.warning("Application databases unavailable at startup: %s", ", ".join(unavailable_apps))

        domain_snapshot = self.domain_registry_snapshot()
        if domain_snapshot.get("enabled"):
            failed_domains = [
                domain_name
                for domain_name, payload in (domain_snapshot.get("domains") or {}).items()
                if isinstance(payload, dict) and str(payload.get("status") or "").strip().lower() != "ok"
            ]
            if failed_domains:
                message = "Configured domains failed to load: " + ", ".join(failed_domains)
                if self._strict_startup_probes_enabled():
                    raise RuntimeError(message)
                logger.warning(message)

        self._warm_semantic_retrieval()

        self._workflow = create_graph(
            router_node=self.router_node,
            intermediate_node=self.intermediate_node,
            chat_node=self.chat_node,
            guardrail_node=self.guardrail_node,
            report_node=self.get_report_node(),
            intent_node=self.intent_node,
            sql_builder_node=self.sql_builder_node,
            sql_validate_node=self.sql_validate_node,
            sql_execute_node=self.sql_execute_node,
            response_node=self.response_node,
        )

    async def shutdown(self) -> None:
        await self.cache.close()
        report_node = self._report_node
        self._report_node = None
        if report_node is not None:
            cache_service = getattr(report_node, "cache_service", None)
            close_cache = getattr(cache_service, "close", None)
            if callable(close_cache):
                try:
                    await close_cache()
                except Exception:
                    logger.exception("Failed to close report cache client during shutdown")
        db_service = self._db_service
        self._db_service = None
        close_db_service = getattr(db_service, "close", None)
        if callable(close_db_service):
            try:
                close_db_service()
            except Exception:
                logger.exception("Failed to close report DB engine during shutdown")
        close_schema_service = getattr(self.schema_service, "close", None)
        if callable(close_schema_service):
            try:
                close_schema_service()
            except Exception:
                logger.exception("Failed to close schema service engines during shutdown")
        self._workflow = None

    def get_workflow(self) -> Optional[Any]:
        return self._workflow

    def get_db_service(self) -> Any:
        if self._db_service is None:
            if DBService is None:
                raise RuntimeError("DBService is not available")
            self._db_service = DBService(db_url=self.settings.DATABASE_URL)
        return self._db_service

    def get_report_node(self) -> ReportNode:
        if self._report_node is None:
            db_service = self.get_db_service()
            self._report_node = ReportNode(
                reporting_service=ReportingService(domain_provider=self.domain_provider),
                db_service=db_service,
                audit_service=AuditService(db_service=db_service),
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
