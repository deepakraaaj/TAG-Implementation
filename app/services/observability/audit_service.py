"""Audit logging service for tracking report executions and user actions."""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from app.config import get_settings
from app.services.interfaces import DBGateway

logger = logging.getLogger(__name__)


class AuditService:
    """
    Service for logging report executions and user actions.
    
    Tracks:
    - Who ran which report
    - When it was executed
    - How long it took
    - Success/failure status
    - Error details if failed
    """

    def __init__(self, db_service: DBGateway, enabled: Optional[bool] = None):
        self.db_service = db_service
        if enabled is None:
            enabled = bool(get_settings().ENABLE_AUDIT_LOGGING)
        self.enabled = bool(enabled)

    @staticmethod
    def _clamp_int(value: Any, *, default: int, minimum: int, maximum: int) -> int:
        try:
            normalized = int(value)
        except (TypeError, ValueError):
            return default
        return max(minimum, min(normalized, maximum))

    async def log_report_execution(
        self,
        company_id: int,
        user_id: int,
        report_id: str,
        report_name: str,
        execution_time_ms: int,
        row_count: int,
        status: str,
        error_message: Optional[str] = None
    ) -> None:
        """
        Log a report execution to the audit table.
        
        Args:
            company_id: Company identifier
            user_id: User who executed the report
            report_id: Report identifier
            report_name: Human-readable report name
            execution_time_ms: Execution time in milliseconds
            row_count: Number of rows returned
            status: 'success' or 'error'
            error_message: Error details if status is 'error'
        """
        if not self.enabled:
            return

        try:
            sql = """
                INSERT INTO report_audit_log (
                    company_id, user_id, report_id, report_name,
                    execution_time_ms, row_count, status, error_message
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            """
            execution_time = self._clamp_int(execution_time_ms, default=0, minimum=0, maximum=86_400_000)
            rows_returned = self._clamp_int(row_count, default=0, minimum=0, maximum=10_000_000)
            params = (
                company_id,
                user_id,
                report_id,
                report_name,
                execution_time,
                rows_returned,
                status,
                error_message,
            )

            self.db_service.execute_update(sql, params)
            logger.info("Audit log created for report_id=%s user_id=%s", report_id, user_id)
        except Exception:
            # Don't fail the report if audit logging fails
            logger.exception("Failed to log audit entry for report_id=%s user_id=%s", report_id, user_id)

    async def get_user_report_history(
        self,
        company_id: int,
        user_id: int,
        limit: int = 50
    ) -> list:
        """Get recent report execution history for a user."""
        if not self.enabled:
            return []

        try:
            limit_value = self._clamp_int(limit, default=50, minimum=1, maximum=500)
            sql = """
                SELECT report_id, report_name, execution_time_ms, row_count,
                       status, created_at
                FROM report_audit_log
                WHERE company_id = %s AND user_id = %s
                ORDER BY created_at DESC
                LIMIT {limit_value}
            """
            return self.db_service.execute_query(sql.format(limit_value=limit_value), (company_id, user_id))
        except Exception:
            logger.exception("Failed to fetch audit history for company_id=%s user_id=%s", company_id, user_id)
            return []

    async def get_report_usage_stats(
        self,
        company_id: int,
        days: int = 30
    ) -> Dict[str, Any]:
        """Get report usage statistics for the last N days."""
        if not self.enabled:
            return {}

        try:
            days_window = self._clamp_int(days, default=30, minimum=1, maximum=365)
            sql = """
                SELECT 
                    report_id,
                    report_name,
                    COUNT(*) as execution_count,
                    AVG(execution_time_ms) as avg_execution_time_ms,
                    SUM(CASE WHEN status = 'success' THEN 1 ELSE 0 END) as success_count,
                    SUM(CASE WHEN status = 'error' THEN 1 ELSE 0 END) as error_count
                FROM report_audit_log
                WHERE company_id = %s 
                  AND created_at >= DATE_SUB(NOW(), INTERVAL {days_window} DAY)
                GROUP BY report_id, report_name
                ORDER BY execution_count DESC
            """
            results = self.db_service.execute_query(sql.format(days_window=days_window), (company_id,))
            return {
                "period_days": days_window,
                "reports": results,
            }
        except Exception:
            logger.exception("Failed to fetch usage stats for company_id=%s", company_id)
            return {}
