from app.assistant.services.sql_builder_service import SQLBuilderService


def test_parse_kv_pairs_supports_equals_and_colon():
    parsed = SQLBuilderService.parse_kv_pairs("id=12, status:Completed")
    assert parsed["id"] == "12"
    assert parsed["status"] == "Completed"


def test_safe_ident_rejects_invalid_name():
    assert SQLBuilderService._safe_ident("status") == "status"
    assert SQLBuilderService._safe_ident("status;drop") == ""
