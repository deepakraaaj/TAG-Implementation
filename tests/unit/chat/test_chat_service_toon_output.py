from app.services.chat import ChatService


def test_chat_service_decorates_sql_rows_preview_with_toon():
    service = ChatService()
    sql_payload = {
        "ran": True,
        "cached": False,
        "query": "SELECT id, name FROM asset LIMIT 2;",
        "row_count": 2,
        "rows_preview": [
            {"id": 1, "name": "Pump-1"},
            {"id": 2, "name": "Pump-2"},
        ],
    }

    out = service._decorate_sql_payload_for_format(sql_payload, {"response_format": "toon"})

    assert out.get("rows_preview_encoding") == "toon"
    assert out.get("rows_preview") == [{"name": "Pump-1"}, {"name": "Pump-2"}]
    assert "{name}" in str(out.get("rows_preview_toon", ""))
    assert "Pump-1" in str(out.get("rows_preview_toon"))
    assert int(out.get("rows_preview_token_count_without_toon", 0)) > 0
    assert int(out.get("rows_preview_token_count_with_toon", 0)) > 0


def test_chat_service_keeps_plain_sql_payload_when_toon_not_requested():
    service = ChatService()
    sql_payload = {
        "ran": True,
        "cached": False,
        "query": "SELECT id, name FROM asset LIMIT 1;",
        "row_count": 1,
        "rows_preview": [{"id": 1, "name": "Pump-1"}],
    }

    out = service._decorate_sql_payload_for_format(sql_payload, {"response_format": "json"})

    assert out.get("rows_preview") == [{"name": "Pump-1"}]
    assert "rows_preview_toon" not in out
    assert "rows_preview_encoding" not in out
    assert int(out.get("rows_preview_token_count_without_toon", 0)) > 0
    assert int(out.get("rows_preview_token_count_with_toon", 0)) > 0


def test_chat_service_handles_empty_rows_preview_without_negative_token_savings():
    service = ChatService()
    sql_payload = {
        "ran": True,
        "cached": False,
        "query": "SELECT id, name FROM asset LIMIT 100;",
        "row_count": 0,
        "rows_preview": [],
    }

    out = service._decorate_sql_payload_for_format(sql_payload, {"response_format": "json"})

    assert int(out.get("rows_preview_token_count_without_toon", -1)) == 0
    assert int(out.get("rows_preview_token_count_with_toon", -1)) == 0
    assert int(out.get("rows_preview_token_saved", -1)) == 0
    assert float(out.get("rows_preview_token_saved_percent", -1)) == 0.0


def test_chat_service_appends_toon_token_summary_to_message():
    service = ChatService()
    payload = {
        "message": "Total 2 assets found. Showing 2.",
        "status": "ok",
        "sql": {
            "rows_preview_token_count_without_toon": 42,
            "rows_preview_token_count_with_toon": 31,
        },
    }

    out = service._append_toon_token_summary_to_message(payload, {"response_format": "toon"})
    msg = str(out.get("message", ""))
    assert msg == "Total 2 assets found. Showing 2."
    assert "with TOON 31" in str(out.get("sql", {}).get("rows_preview_token_summary", ""))
    assert "without TOON 42" in str(out.get("sql", {}).get("rows_preview_token_summary", ""))


def test_chat_service_appends_token_summary_even_without_toon_mode():
    service = ChatService()
    payload = {
        "message": "Total 10 assets found.",
        "status": "ok",
        "sql": {
            "rows_preview_token_count_without_toon": 100,
            "rows_preview_token_count_with_toon": 72,
        },
    }

    out = service._append_toon_token_summary_to_message(payload, {"response_format": "json"})
    msg = str(out.get("message", ""))
    assert msg == "Total 10 assets found."
    assert "with TOON 72" in str(out.get("sql", {}).get("rows_preview_token_summary", ""))
    assert "without TOON 100" in str(out.get("sql", {}).get("rows_preview_token_summary", ""))


def test_chat_service_skips_preview_token_summary_when_preview_is_empty():
    service = ChatService()
    payload = {
        "message": "No records found.",
        "status": "ok",
        "sql": {
            "rows_preview_token_count_without_toon": 0,
            "rows_preview_token_count_with_toon": 0,
        },
    }

    out = service._append_toon_token_summary_to_message(payload, {"response_format": "json"})
    assert "rows_preview_token_summary" not in dict(out.get("sql") or {})


def test_chat_service_appends_llm_token_summary_to_message():
    service = ChatService()
    payload = {
        "message": "Total 10 assets found.",
        "status": "ok",
        "token_usage": {
            "llm_calls": 2,
            "prompt_tokens_est_with_toon": 120,
            "prompt_tokens_est_without_toon": 180,
            "prompt_tokens_est_saved": 60,
        },
    }

    out = service._append_llm_token_summary_to_message(payload)
    msg = str(out.get("message", ""))
    assert msg == "Total 10 assets found."
    summary = str(out.get("token_details", {}).get("llm_prompt_token_summary", ""))
    assert "LLM prompt token estimate" in summary
    assert "with TOON 120" in summary
    assert "without TOON 180" in summary
    assert "Saved 60 tokens" in summary
