from app.services.chat_service import ChatService


def test_parse_load_more_request_with_offset():
    limit, offset = ChatService._parse_load_more_request("Show the next 15 records for the previous query. (Offset: 20)")
    assert limit == 15
    assert offset == 20


def test_parse_load_more_request_defaults():
    limit, offset = ChatService._parse_load_more_request("load more")
    assert limit == 20
    assert offset is None


def test_apply_limit_offset_rewrites_existing_limit():
    sql = "SELECT id, status FROM task_transaction WHERE company_id=1 AND status=2 LIMIT 100;"
    out = ChatService._apply_limit_offset(sql, 15, 20)
    assert out.endswith("LIMIT 15 OFFSET 20;")
    assert "LIMIT 100" not in out


def test_bounded_page_limit_clamps_to_max():
    service = ChatService()
    service.max_page_size = 25
    assert service._bounded_page_limit(None) == 20
    assert service._bounded_page_limit(10) == 10
    assert service._bounded_page_limit(999) == 25
