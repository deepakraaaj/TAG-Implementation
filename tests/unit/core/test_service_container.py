import asyncio
from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from app.core.dependencies import service_container as service_container_module
from app.core.dependencies.service_container import ServiceContainer


class _CloseTracker:
    def __init__(self):
        self.closed = False

    async def close(self):
        self.closed = True


class _SyncCloseTracker:
    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True


class _StartupSettings:
    def __init__(self):
        self.APP_ENV = "test"
        self.DATABASE_URL = "sqlite:///tmp/test.sqlite3"
        self.CACHE_ENABLED = False
        self.CACHE_TTL_SECONDS = 300
        self.REDIS_URL = ""
        self.validated = False

    def validate_runtime(self):
        self.validated = True


class _SchemaTracker:
    def __init__(self, ping_ok=True):
        self.ping_ok = ping_ok
        self.closed = False
        self.ping_calls = []

    def ping(self, db_url=None):
        self.ping_calls.append(db_url)
        return self.ping_ok

    def close(self):
        self.closed = True


class _FakeCache:
    def __init__(self):
        self.connected = False
        self.closed = False

    async def connect(self):
        self.connected = True

    async def close(self):
        self.closed = True

    def is_configured(self):
        return False


class _DBTracker(_SyncCloseTracker):
    def __init__(self, ping_ok=True):
        super().__init__()
        self.ping_ok = ping_ok
        self.ping_calls = 0

    def ping(self):
        self.ping_calls += 1
        return self.ping_ok


def test_shutdown_closes_report_cache_and_clears_workflow():
    shared_cache = _CloseTracker()
    report_cache = _CloseTracker()
    schema_service = _SyncCloseTracker()
    db_service = _DBTracker()
    container = ServiceContainer.__new__(ServiceContainer)
    container.cache = shared_cache
    container.schema_service = schema_service
    container._db_service = db_service
    container._report_node = SimpleNamespace(cache_service=report_cache)
    container._workflow = object()

    asyncio.run(ServiceContainer.shutdown(container))

    assert shared_cache.closed is True
    assert report_cache.closed is True
    assert schema_service.closed is True
    assert db_service.closed is True
    assert container._report_node is None
    assert container._db_service is None
    assert container._workflow is None


def test_startup_validates_runtime_and_builds_workflow(monkeypatch: pytest.MonkeyPatch):
    cache = _FakeCache()
    schema_service = _SchemaTracker(ping_ok=True)
    settings = _StartupSettings()
    report_db = _DBTracker(ping_ok=True)
    container = ServiceContainer.__new__(ServiceContainer)
    container.cache = cache
    container.schema_service = schema_service
    container.settings = settings
    container._workflow = None
    container._db_service = report_db
    container._report_node = None
    container.router_node = object()
    container.intermediate_node = object()
    container.chat_node = object()
    container.guardrail_node = object()
    container.intent_node = object()
    container.sql_builder_node = object()
    container.sql_validate_node = object()
    container.sql_execute_node = object()
    container.response_node = object()
    container.get_report_node = lambda: object()

    monkeypatch.setattr(service_container_module, "DBService", object())
    monkeypatch.setattr(service_container_module, "create_graph", lambda **_kwargs: "workflow")

    asyncio.run(ServiceContainer.startup(container))

    assert settings.validated is True
    assert schema_service.ping_calls == [settings.DATABASE_URL]
    assert report_db.ping_calls == 1
    assert cache.connected is True
    assert container._workflow == "workflow"


def test_startup_fails_fast_when_primary_database_is_unreachable(monkeypatch: pytest.MonkeyPatch):
    cache = _FakeCache()
    schema_service = _SchemaTracker(ping_ok=False)
    settings = _StartupSettings()
    container = ServiceContainer.__new__(ServiceContainer)
    container.cache = cache
    container.schema_service = schema_service
    container.settings = settings
    container._workflow = None
    container._db_service = _DBTracker(ping_ok=True)

    monkeypatch.setattr(service_container_module, "DBService", object())

    with pytest.raises(RuntimeError, match="Primary database is not reachable"):
        asyncio.run(ServiceContainer.startup(container))

    assert settings.validated is True
    assert cache.connected is False


def test_readiness_snapshot_reports_database_failure():
    cache = _FakeCache()
    schema_service = _SchemaTracker(ping_ok=False)
    settings = _StartupSettings()
    report_db = _DBTracker(ping_ok=False)
    container = ServiceContainer.__new__(ServiceContainer)
    container.cache = cache
    container.schema_service = schema_service
    container.settings = settings
    container._workflow = object()
    container._db_service = report_db

    payload = asyncio.run(ServiceContainer.readiness_snapshot(container))

    assert payload["ready"] is False
    assert payload["status"] == "not_ready"
    assert payload["checks"]["config"]["status"] == "ok"
    assert payload["checks"]["database"]["status"] == "not_ready"
    assert payload["checks"]["reporting"]["status"] == "degraded"


def test_warm_semantic_retrieval_iterates_configured_domains(monkeypatch: pytest.MonkeyPatch):
    entered_domains = []
    warmed_domains = []

    @contextmanager
    def _fake_use_domain(domain_name):
        entered_domains.append(domain_name)
        yield

    container = ServiceContainer.__new__(ServiceContainer)
    container.settings = SimpleNamespace(DOMAIN="vts")
    container.app_registry = SimpleNamespace(
        enabled=lambda: True,
        list_apps=lambda: [
            ("vts", SimpleNamespace(domain_name="vts")),
            ("fits", SimpleNamespace(domain_name="fits_dev_march_9")),
        ],
    )
    container.semantic_retriever = SimpleNamespace(
        is_enabled=lambda: True,
        warmup=lambda: warmed_domains.append(entered_domains[-1]) or {"artifacts": 3, "indexed": 2},
    )

    monkeypatch.setattr(
        service_container_module,
        "DomainRegistry",
        SimpleNamespace(use_domain=_fake_use_domain),
    )

    ServiceContainer._warm_semantic_retrieval(container)

    assert entered_domains == ["fits_dev_march_9", "vts"]
    assert warmed_domains == ["fits_dev_march_9", "vts"]
