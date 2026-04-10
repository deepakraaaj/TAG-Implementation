from types import SimpleNamespace

from app.schemas.onboarding import SimpleOnboardingRequest
from app.services.onboarding.simple_service import SimpleOnboardingService


def _snapshot() -> dict:
    return {
        "database_target": "mysql://db.example.com:3306/ops",
        "table_count": 6,
        "tables": [
            {
                "name": "person",
                "columns": [
                    {"name": "id", "type": "INTEGER", "nullable": False},
                    {"name": "first_name", "type": "VARCHAR", "nullable": False},
                    {"name": "last_name", "type": "VARCHAR", "nullable": True},
                    {"name": "email", "type": "VARCHAR", "nullable": True},
                ],
                "primary_key": ["id"],
                "foreign_keys": [],
            },
            {
                "name": "facility",
                "columns": [
                    {"name": "id", "type": "INTEGER", "nullable": False},
                    {"name": "name", "type": "VARCHAR", "nullable": False},
                    {"name": "city", "type": "VARCHAR", "nullable": True},
                ],
                "primary_key": ["id"],
                "foreign_keys": [],
            },
            {
                "name": "task_transaction",
                "columns": [
                    {"name": "id", "type": "INTEGER", "nullable": False},
                    {"name": "title", "type": "VARCHAR", "nullable": False},
                    {"name": "status", "type": "VARCHAR", "nullable": False},
                    {"name": "priority", "type": "VARCHAR", "nullable": True},
                    {"name": "scheduled_date", "type": "DATETIME", "nullable": True},
                    {"name": "assignee_id", "type": "INTEGER", "nullable": True},
                    {"name": "facility_id", "type": "INTEGER", "nullable": True},
                ],
                "primary_key": ["id"],
                "foreign_keys": [
                    {
                        "constrained_columns": ["assignee_id"],
                        "referred_table": "person",
                        "referred_columns": ["id"],
                    },
                    {
                        "constrained_columns": ["facility_id"],
                        "referred_table": "facility",
                        "referred_columns": ["id"],
                    },
                ],
            },
            {
                "name": "task_status",
                "columns": [
                    {"name": "id", "type": "INTEGER", "nullable": False},
                    {"name": "name", "type": "VARCHAR", "nullable": False},
                    {"name": "display_order", "type": "INTEGER", "nullable": True},
                ],
                "primary_key": ["id"],
                "foreign_keys": [],
            },
            {
                "name": "task_asset_mapping",
                "columns": [
                    {"name": "id", "type": "INTEGER", "nullable": False},
                    {"name": "task_transaction_id", "type": "INTEGER", "nullable": False},
                    {"name": "asset_id", "type": "INTEGER", "nullable": False},
                ],
                "primary_key": ["id"],
                "foreign_keys": [
                    {
                        "constrained_columns": ["task_transaction_id"],
                        "referred_table": "task_transaction",
                        "referred_columns": ["id"],
                    },
                    {
                        "constrained_columns": ["asset_id"],
                        "referred_table": "asset",
                        "referred_columns": ["id"],
                    },
                ],
            },
            {
                "name": "audit_log",
                "columns": [
                    {"name": "id", "type": "INTEGER", "nullable": False},
                    {"name": "message", "type": "VARCHAR", "nullable": True},
                ],
                "primary_key": ["id"],
                "foreign_keys": [],
            },
        ],
    }


class _FakeInspector:
    def __init__(self, snapshot: dict):
        self._tables = {table["name"]: table for table in snapshot["tables"]}

    def get_table_names(self):
        return sorted(self._tables)

    def get_columns(self, table_name):
        return list(self._tables[table_name]["columns"])

    def get_pk_constraint(self, table_name):
        return {"constrained_columns": list(self._tables[table_name].get("primary_key") or [])}

    def get_foreign_keys(self, table_name):
        return list(self._tables[table_name].get("foreign_keys") or [])


class _FakeConnection:
    def __enter__(self):
        return self

    def __exit__(self, _exc_type, _exc, _tb):
        return False


class _FakeEngine:
    def connect(self):
        return _FakeConnection()


class _FakeSchemaService:
    default_db_url = "mysql+aiomysql://demo_user:secret@db.example.com:3306/ops"

    def get_engine_for_url(self, _db_url=None):
        return _FakeEngine()


def test_simple_onboarding_builds_artifact_from_live_schema(monkeypatch):
    snapshot = _snapshot()
    monkeypatch.setattr(
        "app.services.onboarding.simple_service.inspect",
        lambda _conn: _FakeInspector(snapshot),
    )

    service = SimpleOnboardingService(schema_service=_FakeSchemaService())
    result = service.build(
        SimpleOnboardingRequest(
            business_context="field service operations",
            selection_mode="review",
        )
    )

    table_map = {table.name: table for table in result.tables}

    assert result.database_target == "mysql+aiomysql://db.example.com:3306/ops"
    assert "task_transaction" in result.categories["Core Operations"]
    assert table_map["person"].category == "People & Teams"
    assert table_map["facility"].category == "Locations & Facilities"
    assert table_map["task_transaction"].description.startswith("Tracks task transaction records")
    assert "audit_log" in result.ignored_tables
    assert "audit_log" not in result.artifact.table_descriptions
    assert result.artifact.business_context == "field service operations"
    assert result.artifact.selected_tables == result.selected_tables
    assert any(
        relationship.from_table == "task_transaction" and relationship.to_table == "person"
        for relationship in result.artifact.relationships
    )
    assert any(
        relationship.from_table == "task_transaction" and relationship.to_table == "facility"
        for relationship in result.artifact.relationships
    )


def test_simple_onboarding_applies_bulk_filters_and_manual_overrides():
    service = SimpleOnboardingService()

    result = service.build_from_snapshot(
        _snapshot(),
        SimpleOnboardingRequest(
            selection_mode="ai",
            include_categories=["Reference Data"],
            bulk_include_patterns=["*mapping*"],
            bulk_exclude_patterns=["*person*"],
        ),
    )

    assert "task_transaction" in result.selected_tables
    assert "facility" in result.selected_tables
    assert "task_status" in result.selected_tables
    assert "task_asset_mapping" in result.selected_tables
    assert "person" not in result.selected_tables
    assert "audit_log" not in result.selected_tables
    assert result.artifact.categories["Reference Data"] == ["task_asset_mapping", "task_status"]
