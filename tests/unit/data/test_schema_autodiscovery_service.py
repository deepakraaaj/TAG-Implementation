"""Unit tests for SchemaAutoDiscoveryService."""
from app.services.data.schema_autodiscovery_service import SchemaAutoDiscoveryService


class _FakeSchemaService:
    """Stub that returns predefined table/column metadata."""

    def __init__(self, tables=None, columns_map=None):
        self._tables = tables or []
        self._columns_map = columns_map or {}

    def get_all_tables(self, db_url=None):
        return list(self._tables)

    def get_table_columns(self, tables, db_url=None):
        return {t: set(self._columns_map.get(t, [])) for t in tables}


def _service(**kwargs):
    return SchemaAutoDiscoveryService(_FakeSchemaService(**kwargs))


# ------------------------------------------------------------------
# build_manifest – basics
# ------------------------------------------------------------------


def test_build_manifest_returns_tables_and_important_columns():
    svc = _service(
        tables=["users", "orders"],
        columns_map={
            "users": ["id", "name", "email"],
            "orders": ["id", "user_id", "total", "company_id"],
        },
    )
    manifest = svc.build_manifest()

    assert "tables" in manifest
    assert "users" in manifest["tables"]
    assert "orders" in manifest["tables"]

    user_cols = set(manifest["tables"]["users"]["important_columns"].keys())
    assert user_cols == {"id", "name", "email"}

    order_cols = set(manifest["tables"]["orders"]["important_columns"].keys())
    assert order_cols == {"id", "user_id", "total", "company_id"}


def test_build_manifest_excludes_noise_tables():
    svc = _service(
        tables=["users", "flyway_schema_history", "alembic_version"],
        columns_map={
            "users": ["id", "name"],
            "flyway_schema_history": ["version"],
            "alembic_version": ["version_num"],
        },
    )
    manifest = svc.build_manifest()
    assert "users" in manifest["tables"]
    assert "flyway_schema_history" not in manifest["tables"]
    assert "alembic_version" not in manifest["tables"]


def test_build_manifest_respects_allowed_tables():
    svc = _service(
        tables=["users", "orders", "products"],
        columns_map={
            "users": ["id", "name"],
            "orders": ["id", "total"],
            "products": ["id", "sku"],
        },
    )
    manifest = svc.build_manifest(allowed_tables=["users", "products"])
    assert set(manifest["tables"].keys()) == {"users", "products"}


def test_build_manifest_generates_aliases():
    svc = _service(
        tables=["task_transaction"],
        columns_map={"task_transaction": ["id", "title"]},
    )
    manifest = svc.build_manifest()
    aliases = manifest["tables"]["task_transaction"]["aliases"]
    assert "task_transaction" in aliases
    assert "task transaction" in aliases


def test_build_manifest_detects_tenant_scope():
    svc = _service(
        tables=["orders"],
        columns_map={"orders": ["id", "total", "company_id"]},
    )
    manifest = svc.build_manifest()
    scope = manifest["tables"]["orders"].get("tenant_scope", {})
    assert scope.get("column") == "company_id"


# ------------------------------------------------------------------
# FK-based join inference
# ------------------------------------------------------------------


def test_build_manifest_infers_joins_via_fk_naming():
    svc = _service(
        tables=["user", "order"],
        columns_map={
            "user": ["id", "name"],
            "order": ["id", "user_id", "total"],
        },
    )
    manifest = svc.build_manifest()
    joins = manifest["tables"]["order"].get("joins", {})
    assert "user" in joins
    assert "order.user_id = user.id" in joins["user"]


def test_build_manifest_does_not_infer_self_join():
    svc = _service(
        tables=["order"],
        columns_map={"order": ["id", "order_id"]},
    )
    manifest = svc.build_manifest()
    joins = manifest["tables"]["order"].get("joins", {})
    assert "order" not in joins


# ------------------------------------------------------------------
# Caching & invalidation
# ------------------------------------------------------------------


def test_build_manifest_caches_result():
    svc = _service(
        tables=["t1"],
        columns_map={"t1": ["id"]},
    )
    first = svc.build_manifest()
    second = svc.build_manifest()
    assert first is second


def test_invalidate_clears_cache():
    svc = _service(
        tables=["t1"],
        columns_map={"t1": ["id"]},
    )
    first = svc.build_manifest()
    svc.invalidate()
    second = svc.build_manifest()
    assert first is not second


def test_empty_database_returns_empty_tables():
    svc = _service(tables=[], columns_map={})
    manifest = svc.build_manifest()
    assert manifest["tables"] == {}
