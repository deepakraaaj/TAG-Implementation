"""Audit logging service for tracking report executions and user actions."""
import logging
from typing import Dict, Any, Optional

from app.config import get_settings
from app.services.interfaces import DBGateway

settings = get_settings()
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

    def __init__(self, db_service: DBGateway):
        self.db_service = db_service
        self.enabled = settings.ENABLE_AUDIT_LOGGING

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
            params = (
                company_id,
                user_id,
                report_id,
                report_name,
                execution_time_ms,
                row_count,
                status,
                error_message
            )
            
            self.db_service.execute_update(sql, params)
            logger.info(f"Audit log created: {report_id} by user {user_id}")
            
        except Exception as e:
            # Don't fail the report if audit logging fails
            logger.error(f"Failed to log audit entry: {e}")

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
            sql = """
                SELECT report_id, report_name, execution_time_ms, row_count,
                       status, created_at
                FROM report_audit_log
                WHERE company_id = %s AND user_id = %s
                ORDER BY created_at DESC
                LIMIT %s
            """
            return self.db_service.execute_query(sql, (company_id, user_id, limit))
        except Exception as e:
            logger.error(f"Failed to fetch audit history: {e}")
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
                  AND created_at >= DATE_SUB(NOW(), INTERVAL %s DAY)
                GROUP BY report_id, report_name
                ORDER BY execution_count DESC
            """
            results = self.db_service.execute_query(sql, (company_id, days))
            
            return {
                "period_days": days,
                "reports": results
            }
        except Exception as e:
            logger.error(f"Failed to fetch usage stats: {e}")
            return {}
