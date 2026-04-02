from types import SimpleNamespace

from sqlalchemy import create_engine, text

from app.services.chat import ChatService
from app.services.chat import service as chat_service_module


class _StaticSchema:
    def __init__(self, engine):
        self._engine = engine

    def get_engine_for_url(self, _db_url=None):
        return self._engine


def test_chat_service_hides_numeric_identifier_columns_from_preview():
    service = ChatService()
    sql_payload = {
        "ran": True,
        "cached": False,
        "query": "SELECT id, name, company_id FROM facility LIMIT 1;",
        "row_count": 1,
        "rows_preview": [{"id": 1, "name": "HQ", "company_id": 56942686}],
    }

    out = service._decorate_sql_payload_for_format(sql_payload, {"response_format": "json"})

    assert out["rows_preview"] == [{"name": "HQ"}]


def test_chat_service_replaces_lookup_ids_with_names(monkeypatch):
    engine = create_engine("sqlite://")
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE user (id INTEGER PRIMARY KEY, company_id INTEGER, first_name TEXT, last_name TEXT)"))
        conn.execute(text("CREATE TABLE facility (id INTEGER PRIMARY KEY, company_id INTEGER, name TEXT)"))
        conn.execute(
            text(
                "INSERT INTO user (id, company_id, first_name, last_name) "
                "VALUES (7, 56942686, 'Anita', 'Shah')"
            )
        )
        conn.execute(
            text(
                "INSERT INTO facility (id, company_id, name) "
                "VALUES (11, 56942686, 'Plant A')"
            )
        )

    service = ChatService(schema_service=_StaticSchema(engine))
    fake_domain = SimpleNamespace(
        get_user_lookup_config=lambda: {
            "table": "user",
            "id_column": "id",
            "first_name_column": "first_name",
            "last_name_column": "last_name",
            "id_filter_key": "assigned_user_id",
            "canonical_filter_key": "assignee",
            "metadata_key": "company_id",
            "tenant_column": "company_id",
            "fallback_name": "User",
        },
        get_config_section=lambda section: {
            "location_lookup": {
                "table": "facility",
                "id_column": "id",
                "name_column": "name",
                "id_filter_keys": ["facility_id"],
                "canonical_filter_key": "location_name",
                "metadata_key": "company_id",
                "tenant_column": "company_id",
            }
        }.get(section, {}),
    )
    monkeypatch.setattr(
        chat_service_module.DomainRegistry,
        "get_current_domain",
        classmethod(lambda cls: fake_domain),
    )

    sql_payload = {
        "ran": True,
        "cached": False,
        "query": "SELECT assigned_user_id, facility_id, status FROM task_transaction LIMIT 1;",
        "row_count": 1,
        "rows_preview": [{"assigned_user_id": 7, "facility_id": 11, "status": "Pending"}],
    }

    out = service._decorate_sql_payload_for_format(sql_payload, {"company_id": 56942686})

    assert out["rows_preview"] == [
        {
            "assignee": "Anita Shah",
            "location_name": "Plant A",
            "status": "Pending",
        }
    ]


def test_chat_service_location_lookup_retries_without_missing_tenant_column(monkeypatch):
    engine = create_engine("sqlite://")
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE location (id INTEGER PRIMARY KEY, name TEXT)"))
        conn.execute(text("INSERT INTO location (id, name) VALUES (21, 'Depot B')"))

    service = ChatService(schema_service=_StaticSchema(engine))
    fake_domain = SimpleNamespace(
        get_user_lookup_config=lambda: {},
        get_config_section=lambda section: {
            "location_lookup": {
                "table": "location",
                "id_column": "id",
                "name_column": "name",
                "id_filter_keys": ["location_id"],
                "canonical_filter_key": "location_name",
                "metadata_key": "company_id",
                "tenant_column": "company_id",
            }
        }.get(section, {}),
    )
    monkeypatch.setattr(
        chat_service_module.DomainRegistry,
        "get_current_domain",
        classmethod(lambda cls: fake_domain),
    )

    sql_payload = {
        "ran": True,
        "cached": False,
        "query": "SELECT location_id, date_created FROM user_location_mapping LIMIT 1;",
        "row_count": 1,
        "rows_preview": [{"location_id": 21, "date_created": "2024-09-25 09:33:52"}],
    }

    out = service._decorate_sql_payload_for_format(sql_payload, {"company_id": 56942686})

    assert out["rows_preview"] == [
        {
            "location_name": "Depot B",
            "date_created": "2024-09-25 09:33:52",
        }
    ]
