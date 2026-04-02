from app.assistant.engine.sql.sql_builder_service import SQLBuilderService
from app.assistant.engine import sql_builder_service as sql_builder_service_module
from app.services.core.toon_service import ToonService
import asyncio


def test_parse_kv_pairs_supports_equals_and_colon():
    parsed = SQLBuilderService.parse_kv_pairs("id=12, status:Completed")
    assert parsed["id"] == "12"
    assert parsed["status"] == "Completed"


def test_safe_ident_rejects_invalid_name():
    assert SQLBuilderService._safe_ident("status") == "status"
    assert SQLBuilderService._safe_ident("status;drop") == ""


class _FakeCatalog:
    @staticmethod
    def important_columns(_table):
        return {"id", "name", "company_id", "created_by", "updated_by", "status", "facility_status"}

    @staticmethod
    def get_query_template(_table, _name):
        return None


class _FakeDomain:
    @staticmethod
    def get_enum_mapping(_column, value):
        col = str(_column).lower()
        text = str(value).strip().lower()
        if col == "status" and text == "open":
            return 0
        if col == "status" and text == "in progress":
            return 1
        if col == "facility_status" and text == "delay in progress":
            return 3
        return value


def _builder_with_fake_catalog():
    builder = object.__new__(SQLBuilderService)
    builder.catalog = _FakeCatalog()
    builder.domain_provider = lambda: _FakeDomain()
    return builder


def test_build_insert_autofills_created_by_and_updated_by():
    builder = _builder_with_fake_catalog()
    sql, err = builder.build_insert("asset", {"name": "Pump"}, company_id=10, actor_user_id=77)
    assert err == ""
    assert "company_id" in sql
    assert "created_by" in sql
    assert "updated_by" in sql
    assert "77" in sql


def test_build_update_autofills_updated_by():
    builder = _builder_with_fake_catalog()
    sql, err = builder.build_update("asset", {"id": 5, "status": "Done"}, company_id=10, actor_user_id=91)
    assert err == ""
    assert "updated_by=91" in sql


def test_build_insert_maps_task_status_enum_values():
    builder = _builder_with_fake_catalog()
    sql, err = builder.build_insert("task_transaction", {"status": "In Progress"}, company_id=10, actor_user_id=77)
    assert err == ""
    assert "status" in sql
    assert " 1" in sql or "(1" in sql or ", 1," in sql


def test_build_update_maps_facility_status_enum_values():
    builder = _builder_with_fake_catalog()
    sql, err = builder.build_update(
        "scheduled_facility_meta_details",
        {"id": 5, "facility_status": "Delay In Progress"},
        company_id=10,
        actor_user_id=91,
    )
    assert err == ""
    assert "facility_status=3" in sql


def test_build_select_ignores_placeholder_null_filters():
    builder = _builder_with_fake_catalog()
    sql, err = builder.build_select_from_filters(
        "task_transaction",
        {"scheduled_date": "null", "status": "null", "name": "pump-1"},
        company_id=56942686,
    )
    assert err == ""
    assert "scheduled_date='null'" not in sql
    assert "status='null'" not in sql
    assert "name='pump-1'" in sql


def test_build_select_keeps_zero_enum_filter_values():
    builder = _builder_with_fake_catalog()
    sql, err = builder.build_select_from_filters(
        "task_transaction",
        {"status": "Open"},
        company_id=56942686,
    )
    assert err == ""
    assert "status=0" in sql


class _FakeDetailCatalog:
    @staticmethod
    def important_columns(_table):
        return {
            "id",
            "vehicle_number",
            "make",
            "model",
            "capacity",
            "remarks",
            "company_id",
            "date_created",
            "date_updated",
        }

    @staticmethod
    def table_meta(_table):
        return {
            "detail_select_columns": [
                "vehicle_number",
                "make",
                "model",
                "capacity",
                "remarks",
                "date_created",
                "date_updated",
            ]
        }

    @staticmethod
    def get_query_template(_table, _name):
        return None


def test_build_select_expands_columns_for_detail_request():
    builder = object.__new__(SQLBuilderService)
    builder.catalog = _FakeDetailCatalog()
    builder.domain_provider = lambda: _FakeDomain()
    sql, err = builder.build_select_from_filters(
        "vehicle",
        {"vehicle_number": "TN55AB1234"},
        company_id=56942673,
        query="Show vehicle details for vehicle number TN55AB1234",
    )
    assert err == ""
    assert "vehicle_number, make, model, capacity, remarks, date_created, date_updated" in sql
    assert "vehicle_number='TN55AB1234'" in sql


class _FakeTaskCatalog:
    @staticmethod
    def important_columns(_table):
        return {"assigned_user_id", "status", "scheduled_date", "priority"}

    @staticmethod
    def get_query_template(_table, _name):
        return (
            "SELECT tt.status, tt.scheduled_date, u.first_name, u.last_name "
            "FROM task_transaction tt "
            "LEFT JOIN user u ON tt.assigned_user_id = u.id "
            "JOIN facility f ON tt.facility_id = f.id "
            "WHERE f.company_id = {company_id} "
            "ORDER BY tt.id DESC LIMIT 100;"
        )


def test_build_select_uses_assigned_user_id_filter():
    builder = object.__new__(SQLBuilderService)
    builder.catalog = _FakeTaskCatalog()
    builder.domain_provider = lambda: _FakeDomain()
    sql, err = builder.build_select_from_filters(
        "task_transaction",
        {"assigned_user_id": 11784788, "scheduled_date": "today"},
        company_id=56942686,
    )
    assert err == ""
    assert "assigned_user_id=11784788" in sql


class _FakeTemplateCatalog:
    @staticmethod
    def important_columns(_table):
        return {"id", "name", "company_id"}

    @staticmethod
    def table_meta(_table):
        return {"default_select_columns": ["id", "name"]}

    @staticmethod
    def get_query_template(_table, template_type):
        if template_type == "detail":
            return "SELECT name FROM asset WHERE company_id = {company_id} ORDER BY id DESC LIMIT 50;"
        if template_type == "list":
            return "SELECT id, name FROM asset WHERE company_id = {company_id} ORDER BY id DESC LIMIT 500;"
        return None


def test_build_select_prefers_dynamic_manifest_metadata_over_list_template():
    builder = object.__new__(SQLBuilderService)
    builder.catalog = _FakeTemplateCatalog()
    builder.domain_provider = lambda: _FakeDomain()

    sql = asyncio.run(builder.build_select("list assets", "asset", 56942686))

    assert sql == "SELECT id, name FROM asset WHERE company_id = 56942686 ORDER BY id DESC LIMIT 100;"


def test_mapping_query_counts_as_generic_list_request_for_mapping_table():
    builder = object.__new__(SQLBuilderService)
    assert builder._is_generic_list_request(
        "Which users are mapped to which locations?",
        "user_location_mapping",
    )


def test_build_select_from_filters_prefers_detail_template_for_detail_request():
    builder = object.__new__(SQLBuilderService)
    builder.catalog = _FakeTemplateCatalog()
    builder.domain_provider = lambda: _FakeDomain()

    sql, err = builder.build_select_from_filters(
        "asset",
        {"name": "Pump-1"},
        company_id=56942686,
        query="show asset details for name Pump-1",
    )

    assert err == ""
    assert "SELECT name FROM asset" in sql
    assert "SELECT id, name FROM asset" not in sql
    assert "name='Pump-1'" in sql


def test_build_select_from_filters_prefers_dynamic_sql_for_simple_filters():
    builder = object.__new__(SQLBuilderService)
    builder.catalog = _FakeTemplateCatalog()
    builder.domain_provider = lambda: _FakeDomain()

    sql, err = builder.build_select_from_filters(
        "asset",
        {"name": "Pump-1"},
        company_id=56942686,
    )

    assert err == ""
    assert sql == "SELECT id, name FROM asset WHERE company_id=56942686 AND name='Pump-1' LIMIT 100;"


def test_build_select_with_usage_reports_toon_prompt_estimates(monkeypatch):
    class _NoMetadataCatalog:
        @staticmethod
        def important_columns(_table):
            return set()

        @staticmethod
        def get_query_template(_table, _template_type):
            return None

    class _FakeResponse:
        content = '{"sql":"SELECT id FROM asset LIMIT 100;"}'
        response_metadata = {
            "token_usage": {
                "prompt_tokens": 32,
                "completion_tokens": 8,
                "total_tokens": 40,
            }
        }

    async def _fake_ainvoke(*_args, **_kwargs):
        return _FakeResponse()

    builder = object.__new__(SQLBuilderService)
    builder.catalog = _NoMetadataCatalog()
    builder.domain_provider = lambda: _FakeDomain()
    builder.llm = object()
    builder.toon = ToonService()
    monkeypatch.setattr(sql_builder_service_module, "ainvoke_with_retry", _fake_ainvoke)

    sql, usage = asyncio.run(
        builder.build_select_with_usage(
            "list assets",
            "asset",
            56942686,
            metadata={"token_minimization": True},
        )
    )

    assert "SELECT id FROM asset LIMIT 100" in sql
    assert int(usage.get("llm_calls", 0)) == 1
    assert int(usage.get("toon_llm_calls", 0)) == 1
    assert int(usage.get("prompt_tokens_est_without_toon", 0)) >= int(
        usage.get("prompt_tokens_est_with_toon", 0)
    )
