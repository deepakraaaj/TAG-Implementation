from app.assistant.services.sql_builder_service import SQLBuilderService


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


def _builder_with_fake_catalog():
    builder = object.__new__(SQLBuilderService)
    builder.catalog = _FakeCatalog()
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
