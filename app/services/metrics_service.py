"""Prometheus metrics service for monitoring report usage and performance."""
import logging
from prometheus_client import Counter, Histogram, Gauge, generate_latest, CONTENT_TYPE_LATEST
from typing import Dict

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
    def get_metrics() -> bytes:
        """Get Prometheus metrics in text format."""
        return generate_latest()

    @staticmethod
    def get_content_type() -> str:
        """Get Prometheus content type."""
        return CONTENT_TYPE_LATEST
