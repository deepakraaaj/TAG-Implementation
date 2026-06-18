import asyncio
from types import SimpleNamespace

from app.assistant.nodes.reporting.report_node import ReportNode


class _FakeReportingService:
    def __init__(self) -> None:
        self.reports = {
            "task_transaction_status_summary": {
                "name": "Task Transactions Status Summary",
                "description": "Task Transactions grouped by status",
                "aliases": ["task status"],
            }
        }

    def check_access(self, report_id: str, user_role: str) -> bool:
        return True

    def get_report_metadata(self, report_id: str):
        return {
            "name": "Task Transactions Status Summary",
            "description": "Task Transactions grouped by status",
        }

    def get_report_query(self, report_id: str, params, filters=None, page=1, page_size=None):
        return "SELECT 'Open' AS status, 1 AS count"

    def get_timeout(self, report_id: str) -> int:
        return 1

    def get_categories(self):
        return ["summary"]

    def list_reports(self, category=None, user_role="user"):
        return []


class _FakeDB:
    def __init__(self) -> None:
        self.query_calls = []

    def execute_query(self, sql, params=None, db_url=None):
        self.query_calls.append((sql, params, db_url))
        return [{"status": "Open", "count": 1}]


class _FakeAudit:
    async def log_report_execution(self, **kwargs):
        return None


class _FakeCache:
    def generate_cache_key(self, **kwargs):
        return "report-cache-key"

    async def get(self, cache_key):
        return None

    async def set(self, cache_key, value, ttl=None):
        return True


class _FakeMetrics:
    def record_cache_hit(self, report_id):
        return None

    def record_cache_miss(self, report_id):
        return None

    def increment_active_queries(self, report_id):
        return None

    def decrement_active_queries(self, report_id):
        return None

    def record_execution(self, report_id, status):
        return None

    def record_execution_time(self, report_id, execution_time_sec):
        return None

    def record_result_size(self, report_id, result_size):
        return None


def test_report_node_executes_against_request_scoped_db_url():
    fake_db = _FakeDB()
    node = ReportNode(
        reporting_service=_FakeReportingService(),
        db_service=fake_db,
        audit_service=_FakeAudit(),
        cache_service=_FakeCache(),
        metrics_service=_FakeMetrics(),
    )

    state = {
        "messages": [SimpleNamespace(content="Task status")],
        "metadata": {
            "company_id": 56942516,
            "user_id": "11784219",
            "user_role": "user",
            "db_connection_string": "mysql+aiomysql://db.example.com:3306/REMP",
        },
    }

    result = asyncio.run(node.run(state))

    assert fake_db.query_calls == [
        (
            "SELECT 'Open' AS status, 1 AS count",
            None,
            "mysql+aiomysql://db.example.com:3306/REMP",
        )
    ]
    assert result["report_result"]["report_id"] == "task_transaction_status_summary"
