import asyncio

from app.services.observability.audit_service import AuditService


class _FakeDB:
    def __init__(self, query_result=None):
        self.query_result = [] if query_result is None else query_result
        self.update_calls = []
        self.query_calls = []

    def execute_update(self, sql, params=None):
        self.update_calls.append((sql, params))

    def execute_query(self, sql, params=None):
        self.query_calls.append((sql, params))
        return self.query_result


def test_audit_service_clamps_values_and_uses_safe_query_windows():
    db = _FakeDB(query_result=[{"report_id": "r-1"}])
    service = AuditService(db, enabled=True)

    asyncio.run(
        service.log_report_execution(
            company_id=3,
            user_id=9,
            report_id="r-1",
            report_name="Summary",
            execution_time_ms=-5,
            row_count=-10,
            status="success",
        )
    )
    history = asyncio.run(service.get_user_report_history(company_id=3, user_id=9, limit=9999))
    stats = asyncio.run(service.get_report_usage_stats(company_id=3, days=0))

    _, update_params = db.update_calls[0]
    history_sql, history_params = db.query_calls[0]
    stats_sql, stats_params = db.query_calls[1]

    assert update_params[4] == 0
    assert update_params[5] == 0
    assert "LIMIT 500" in history_sql
    assert history_params == (3, 9)
    assert "INTERVAL 1 DAY" in stats_sql
    assert stats_params == (3,)
    assert history == [{"report_id": "r-1"}]
    assert stats["period_days"] == 1


def test_audit_service_returns_empty_payloads_when_disabled():
    db = _FakeDB()
    service = AuditService(db, enabled=False)

    history = asyncio.run(service.get_user_report_history(company_id=1, user_id=2))
    stats = asyncio.run(service.get_report_usage_stats(company_id=1))

    assert history == []
    assert stats == {}
    assert db.query_calls == []
