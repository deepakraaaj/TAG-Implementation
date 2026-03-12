"""Prometheus metrics service for monitoring report usage and performance."""
import logging
from typing import Dict

try:
    from prometheus_client import Counter, Histogram, Gauge, generate_latest, CONTENT_TYPE_LATEST
except ModuleNotFoundError:
    CONTENT_TYPE_LATEST = "text/plain; version=0.0.4; charset=utf-8"

    class _NoopMetric:
        def labels(self, **_kwargs):
            return self

        def inc(self, _value=1):
            return None

        def dec(self):
            return None

        def observe(self, _value):
            return None

    def Counter(*_args, **_kwargs):  # type: ignore
        return _NoopMetric()

    def Histogram(*_args, **_kwargs):  # type: ignore
        return _NoopMetric()

    def Gauge(*_args, **_kwargs):  # type: ignore
        return _NoopMetric()

    def generate_latest() -> bytes:  # type: ignore
        return b""

logger = logging.getLogger(__name__)


# Define metrics
report_executions_total = Counter(
    'report_executions_total',
    'Total number of report executions',
    ['report_id', 'status']
)

report_cache_hits_total = Counter(
    'report_cache_hits_total',
    'Total number of cache hits',
    ['report_id']
)

report_cache_misses_total = Counter(
    'report_cache_misses_total',
    'Total number of cache misses',
    ['report_id']
)

report_execution_duration_seconds = Histogram(
    'report_execution_duration_seconds',
    'Report execution time in seconds',
    ['report_id'],
    buckets=(0.1, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0, 60.0)
)

report_result_size_bytes = Histogram(
    'report_result_size_bytes',
    'Size of report results in bytes',
    ['report_id'],
    buckets=(1024, 10240, 102400, 1024000, 10240000)
)

report_active_queries = Gauge(
    'report_active_queries',
    'Number of currently active report queries',
    ['report_id']
)

chat_requests_total = Counter(
    'chat_requests_total',
    'Total number of chat terminal responses by status and source',
    ['status', 'source']
)

chat_request_latency_seconds = Histogram(
    'chat_request_latency_seconds',
    'Chat request terminal latency in seconds',
    ['status', 'source'],
    buckets=(0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0)
)

chat_stage_latency_seconds = Histogram(
    'chat_stage_latency_seconds',
    'Per-stage chat latency in seconds',
    ['stage'],
    buckets=(0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0)
)

chat_timeouts_total = Counter(
    'chat_timeouts_total',
    'Total number of chat timeout failures by stage',
    ['stage']
)

chat_idempotency_replays_total = Counter(
    'chat_idempotency_replays_total',
    'Total number of idempotent replay responses served'
)

chat_mutation_denied_total = Counter(
    'chat_mutation_denied_total',
    'Total number of denied mutation requests',
    ['reason']
)

guardrails_verifier_pass_total = Counter(
    'guardrails_verifier_pass_total',
    'Total number of verifier passes by route',
    ['route']
)

guardrails_verifier_fail_total = Counter(
    'guardrails_verifier_fail_total',
    'Total number of verifier non-pass outcomes by route and status',
    ['route', 'status']
)

guardrails_validator_fail_total = Counter(
    'guardrails_validator_fail_total',
    'Total number of validator failures by route and reason',
    ['route', 'reason']
)

guardrails_abstain_total = Counter(
    'guardrails_abstain_total',
    'Total number of abstentions enforced by guardrails',
    ['route']
)

guardrails_clarify_total = Counter(
    'guardrails_clarify_total',
    'Total number of clarification responses enforced by guardrails',
    ['route']
)

guardrails_estimated_tokens_saved_total = Counter(
    'guardrails_estimated_tokens_saved_total',
    'Estimated prompt tokens saved by compact guardrail context'
)


class MetricsService:
    """
    Prometheus metrics service for monitoring.
    
    Tracks:
    - Report execution counts (success/error)
    - Cache hit/miss rates
    - Execution duration
    - Result sizes
    - Active queries
    """

    @staticmethod
    def record_execution(report_id: str, status: str):
        """Record a report execution."""
        report_executions_total.labels(report_id=report_id, status=status).inc()

    @staticmethod
    def record_cache_hit(report_id: str):
        """Record a cache hit."""
        report_cache_hits_total.labels(report_id=report_id).inc()

    @staticmethod
    def record_cache_miss(report_id: str):
        """Record a cache miss."""
        report_cache_misses_total.labels(report_id=report_id).inc()

    @staticmethod
    def record_execution_time(report_id: str, duration_seconds: float):
        """Record execution time."""
        report_execution_duration_seconds.labels(report_id=report_id).observe(duration_seconds)

    @staticmethod
    def record_result_size(report_id: str, size_bytes: int):
        """Record result size."""
        report_result_size_bytes.labels(report_id=report_id).observe(size_bytes)

    @staticmethod
    def increment_active_queries(report_id: str):
        """Increment active query count."""
        report_active_queries.labels(report_id=report_id).inc()

    @staticmethod
    def decrement_active_queries(report_id: str):
        """Decrement active query count."""
        report_active_queries.labels(report_id=report_id).dec()

    @staticmethod
    def record_chat_request(status: str, duration_seconds: float, source: str = "live"):
        normalized_status = str(status or "unknown").strip().lower() or "unknown"
        normalized_source = str(source or "live").strip().lower() or "live"
        chat_requests_total.labels(status=normalized_status, source=normalized_source).inc()
        chat_request_latency_seconds.labels(status=normalized_status, source=normalized_source).observe(
            max(0.0, float(duration_seconds or 0.0))
        )

    @staticmethod
    def record_chat_stage_latency(stage: str, duration_seconds: float):
        normalized_stage = str(stage or "unknown").strip().lower() or "unknown"
        chat_stage_latency_seconds.labels(stage=normalized_stage).observe(max(0.0, float(duration_seconds or 0.0)))

    @staticmethod
    def record_chat_timeout(stage: str = "unknown"):
        normalized_stage = str(stage or "unknown").strip().lower() or "unknown"
        chat_timeouts_total.labels(stage=normalized_stage).inc()

    @staticmethod
    def record_idempotency_replay():
        chat_idempotency_replays_total.inc()

    @staticmethod
    def record_mutation_denied(reason: str = "policy"):
        normalized_reason = str(reason or "policy").strip().lower() or "policy"
        chat_mutation_denied_total.labels(reason=normalized_reason).inc()

    @staticmethod
    def record_guardrail_verifier(route: str, status: str) -> None:
        normalized_route = str(route or "unknown").strip().upper() or "UNKNOWN"
        normalized_status = str(status or "pass").strip().lower() or "pass"
        if normalized_status == "pass":
            guardrails_verifier_pass_total.labels(route=normalized_route).inc()
        else:
            guardrails_verifier_fail_total.labels(route=normalized_route, status=normalized_status).inc()

    @staticmethod
    def record_guardrail_validator_failure(route: str, reason: str) -> None:
        normalized_route = str(route or "unknown").strip().upper() or "UNKNOWN"
        normalized_reason = str(reason or "validation_failed").strip().lower() or "validation_failed"
        guardrails_validator_fail_total.labels(route=normalized_route, reason=normalized_reason).inc()

    @staticmethod
    def record_guardrail_abstain(route: str) -> None:
        normalized_route = str(route or "unknown").strip().upper() or "UNKNOWN"
        guardrails_abstain_total.labels(route=normalized_route).inc()

    @staticmethod
    def record_guardrail_clarify(route: str) -> None:
        normalized_route = str(route or "unknown").strip().upper() or "UNKNOWN"
        guardrails_clarify_total.labels(route=normalized_route).inc()

    @staticmethod
    def record_guardrail_tokens_saved(tokens_saved: int) -> None:
        try:
            value = int(tokens_saved)
        except Exception:
            value = 0
        if value > 0:
            guardrails_estimated_tokens_saved_total.inc(value)

    @staticmethod
    def get_metrics() -> bytes:
        """Get Prometheus metrics in text format."""
        return generate_latest()

    @staticmethod
    def get_content_type() -> str:
        """Get Prometheus content type."""
        return CONTENT_TYPE_LATEST
