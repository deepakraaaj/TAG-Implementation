from types import SimpleNamespace

from app.assistant.nodes.reporting.report_node import ReportNode


def _build_node():
    return ReportNode(
        reporting_service=SimpleNamespace(
            reports={
                "task_transaction_status_summary": {
                    "name": "Task Transactions Status Summary",
                    "aliases": ["task status", "task status summary"],
                }
            }
        ),
        db_service=SimpleNamespace(),
        audit_service=SimpleNamespace(),
        cache_service=SimpleNamespace(),
        metrics_service=SimpleNamespace(),
    )


def test_match_report_supports_exact_alias():
    node = _build_node()

    assert node._match_report("task status") == "task_transaction_status_summary"


def test_match_report_does_not_overmatch_alias_with_extra_filters():
    node = _build_node()

    assert node._match_report("show task status for nirmala") == ""
