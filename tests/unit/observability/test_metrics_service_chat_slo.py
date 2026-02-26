from app.services.observability.metrics_service import MetricsService


def test_metrics_service_chat_slo_metrics_exposed():
    svc = MetricsService()
    svc.record_chat_request(status="ok", duration_seconds=0.12, source="live")
    svc.record_chat_stage_latency(stage="workflow_execution", duration_seconds=0.08)
    svc.record_chat_timeout(stage="workflow_execution")
    svc.record_idempotency_replay()
    svc.record_mutation_denied(reason="role_or_policy")

    payload = svc.get_metrics().decode("utf-8")
    if payload:
        assert "chat_requests_total" in payload
        assert "chat_request_latency_seconds" in payload
        assert "chat_stage_latency_seconds" in payload
        assert "chat_timeouts_total" in payload
        assert "chat_idempotency_replays_total" in payload
        assert "chat_mutation_denied_total" in payload
