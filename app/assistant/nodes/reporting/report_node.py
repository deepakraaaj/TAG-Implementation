"""Production-ready report generation node with audit logging, RBAC, and error handling."""
import logging
import time
import sys
from typing import Dict, Any, Optional
import asyncio

from langchain_core.messages import AIMessage

from app.assistant.engine.reporting.reporting_service import ReportingService
from app.services.interfaces import AuditLogger, DBGateway, ReportCacheBackend
from app.services.observability.metrics_service import MetricsService
from app.config import get_settings

settings = get_settings()
logger = logging.getLogger(__name__)


class ReportNode:
    """
    Production-ready report execution node.
    
    Features:
    - Pagination support
    - Role-based access control
    - Audit logging
    - Error handling with retry logic
    - Query timeout enforcement
    - Redis caching
    - Prometheus metrics
    """

    def __init__(
        self,
        reporting_service: ReportingService,
        db_service: DBGateway,
        audit_service: AuditLogger,
        cache_service: ReportCacheBackend,
        metrics_service: MetricsService,
    ):
        self.reporting_service = reporting_service
        self.db_service = db_service
        self.audit_service = audit_service
        self.cache_service = cache_service
        self.metrics_service = metrics_service

    async def run(self, state: Dict) -> Dict:
        """Execute report based on user request."""
        messages = state.get("messages", [])
        query = str(messages[-1].content) if messages else ""
        metadata = state.get("metadata", {})
        company_id = metadata.get("company_id")
        user_id = metadata.get("user_id") or metadata.get("userId")
        user_role = metadata.get("role") or metadata.get("user_role") or metadata.get("userRole") or "user"

        # Check if user is asking for report list
        if self._is_report_list_request(query):
            return self._list_reports(user_role)

        # Try to match query to a report
        report_id = self._match_report(query)
        logger.info(f"Report matching for query '{query}': {report_id}")
        
        if not report_id:
            return {
                "messages": [AIMessage(content=self._get_available_reports_message(user_role))],
                "report_result": None
            }

        # Check access permissions
        if not self.reporting_service.check_access(report_id, user_role):
            logger.warning(f"Access denied: user {user_id} (role: {user_role}) tried to access {report_id}")
            return {
                "messages": [AIMessage(content=f"⛔ **Access Denied**\n\nYou don't have permission to run this report. This report requires '{self.reporting_service.reports[report_id].get('access_level', 'user')}' access level.")],
                "report_result": None,
                "error": "Access denied",
            }

        # Extract pagination parameters and dynamic filters from query
        page, page_size = self._extract_pagination(query)
        filters = self._extract_filters(query)
        logger.info(f"Extracted filters for {report_id}: {filters}")

        # Execute report with retry logic
        return await self._execute_report_with_retry(
            report_id=report_id,
            company_id=company_id,
            user_id=user_id,
            user_role=user_role,
            filters=filters,
            page=page,
            page_size=page_size
        )

    async def _execute_report_with_retry(
        self,
        report_id: str,
        company_id: int,
        user_id: int,
        user_role: str,
        filters: Optional[Dict[str, Any]] = None,
        page: int = 1,
        page_size: Optional[int] = None,
        max_retries: int = 3
    ) -> Dict:
        """Execute report with retry logic for transient failures."""
        params = {
            "company_id": company_id,
            "user_id": user_id
        }
        
        report_metadata = self.reporting_service.get_report_metadata(report_id)
        start_time = time.time()

        # Generate cache key (including filters)
        cache_key = self.cache_service.generate_cache_key(
            report_id=report_id,
            company_id=company_id,
            page=page,
            page_size=page_size or settings.DEFAULT_PAGE_SIZE,
            user_id=user_id,
            filters=filters # Cache service should handle this
        )
        
        # Check cache first
        cached_results = await self.cache_service.get(cache_key)
        if cached_results:
            # Record cache hit
            self.metrics_service.record_cache_hit(report_id)
            
            execution_time_ms = int((time.time() - start_time) * 1000)
            logger.info(f"Report {report_id} served from cache ({execution_time_ms}ms)")
            
            message = self._format_report_results(
                report_metadata, cached_results, page, page_size
            )
            message += "\n\n💾 *Served from cache*"
            
            return {
                "messages": [AIMessage(content=message)],
                "report_result": {
                    "report_id": report_id,
                    "report_name": report_metadata.get("name"),
                    "results": cached_results,
                    "page": page,
                    "page_size": page_size or settings.DEFAULT_PAGE_SIZE,
                    "execution_time_ms": execution_time_ms,
                    "cached": True
                }
            }
        
        # Record cache miss
        self.metrics_service.record_cache_miss(report_id)
        
        # Increment active queries
        self.metrics_service.increment_active_queries(report_id)
        
        try:
            for attempt in range(max_retries):
                try:
                    # Get query with pagination and filters
                    sql_query = self.reporting_service.get_report_query(
                        report_id, 
                        params, 
                        filters=filters,
                        page=page, 
                        page_size=page_size
                    )
                    
                    if not sql_query:
                        await self._log_audit(
                            company_id, user_id, report_id, report_metadata.get("name"),
                            0, 0, "error", "Failed to generate query"
                        )
                        self.metrics_service.record_execution(report_id, "error")
                        return {
                            "messages": [AIMessage(content=f"❌ Failed to generate report query for: {report_id}")],
                            "report_result": None,
                            "error": "Failed to generate report query",
                        }

                    # Execute query with timeout
                    timeout = self.reporting_service.get_timeout(report_id)
                    results = await asyncio.wait_for(
                        asyncio.to_thread(self.db_service.execute_query, sql_query),
                        timeout=timeout
                    )
                    
                    # Cache results
                    await self.cache_service.set(cache_key, results)
                    
                    # Success - log audit and return
                    execution_time_ms = int((time.time() - start_time) * 1000)
                    execution_time_sec = execution_time_ms / 1000.0
                    
                    # Record metrics
                    self.metrics_service.record_execution(report_id, "success")
                    self.metrics_service.record_execution_time(report_id, execution_time_sec)
                    
                    # Calculate result size
                    result_size = sys.getsizeof(str(results))
                    self.metrics_service.record_result_size(report_id, result_size)
                    
                    await self._log_audit(
                        company_id, user_id, report_id, report_metadata.get("name"),
                        execution_time_ms, len(results), "success", None
                    )
                    
                    message = self._format_report_results(
                        report_metadata, results, page, page_size
                    )
                    return {
                        "messages": [AIMessage(content=message)],
                        "report_result": {
                            "report_id": report_id,
                            "report_name": report_metadata.get("name"),
                            "results": results,
                            "page": page,
                            "page_size": page_size or settings.DEFAULT_PAGE_SIZE,
                            "execution_time_ms": execution_time_ms
                        }
                    }
                    
                except asyncio.TimeoutError:
                    error_msg = f"Query timeout after {timeout}s"
                    logger.error(f"Report {report_id} timed out: {error_msg}")
                    self.metrics_service.record_execution(report_id, "timeout")
                    await self._log_audit(
                        company_id, user_id, report_id, report_metadata.get("name"),
                        int((time.time() - start_time) * 1000), 0, "error", error_msg
                    )
                    return {
                        "messages": [AIMessage(content=f"⏱️ **Query Timeout**\n\nThe report took too long to execute (>{timeout}s). Try:\n- Adding more specific filters\n- Reducing the date range\n- Running during off-peak hours")],
                        "report_result": None,
                        "error": error_msg,
                    }
                    
                except Exception as e:
                    error_type = type(e).__name__
                    error_msg = str(e)
                    
                    # Check if this is a transient error worth retrying
                    is_transient = self._is_transient_error(e)
                    
                    if is_transient and attempt < max_retries - 1:
                        # Retry with exponential backoff
                        wait_time = 2 ** attempt
                        logger.warning(f"Transient error on attempt {attempt + 1}/{max_retries}: {error_msg}. Retrying in {wait_time}s...")
                        await asyncio.sleep(wait_time)
                        continue
                    else:
                        # Permanent error or max retries reached
                        logger.error(f"Report execution failed: {error_type}: {error_msg}")
                        self.metrics_service.record_execution(report_id, "error")
                        await self._log_audit(
                            company_id, user_id, report_id, report_metadata.get("name"),
                            int((time.time() - start_time) * 1000), 0, "error", f"{error_type}: {error_msg}"
                        )
                        
                        user_friendly_msg = self._get_user_friendly_error(e)
                        return {
                            "messages": [AIMessage(content=f"❌ **Report Execution Failed**\n\n{user_friendly_msg}")],
                            "report_result": None,
                            "error": f"{error_type}: {error_msg}",
                        }
            
            # Should never reach here, but just in case
            return {
                "messages": [AIMessage(content="❌ Report execution failed after multiple retries.")],
                "report_result": None,
                "error": "Report execution failed after multiple retries.",
            }
        finally:
            # Always decrement active queries
            self.metrics_service.decrement_active_queries(report_id)

    def _is_transient_error(self, error: Exception) -> bool:
        """Check if error is transient and worth retrying."""
        error_type = type(error).__name__
        error_msg = str(error).lower()
        
        transient_patterns = [
            "connection",
            "timeout",
            "deadlock",
            "lock wait",
            "too many connections"
        ]
        
        return any(pattern in error_msg for pattern in transient_patterns)

    def _get_user_friendly_error(self, error: Exception) -> str:
        """Convert technical error to user-friendly message."""
        error_msg = str(error).lower()
        
        if "syntax" in error_msg or "sql" in error_msg:
            return "There was an issue with the report query. Please contact support."
        elif "permission" in error_msg or "denied" in error_msg:
            return "Database permission error. Please contact your administrator."
        elif "connection" in error_msg:
            return "Database connection issue. Please try again in a moment."
        else:
            return f"An unexpected error occurred: {str(error)}"

    async def _log_audit(
        self,
        company_id: int,
        user_id: int,
        report_id: str,
        report_name: str,
        execution_time_ms: int,
        row_count: int,
        status: str,
        error_message: Optional[str]
    ):
        """Log report execution to audit table."""
        try:
            await self.audit_service.log_report_execution(
                company_id=company_id,
                user_id=user_id,
                report_id=report_id,
                report_name=report_name,
                execution_time_ms=execution_time_ms,
                row_count=row_count,
                status=status,
                error_message=error_message
            )
        except Exception as e:
            # Don't fail the report if audit logging fails
            logger.error(f"Audit logging failed: {e}")

    def _extract_pagination(self, query: str) -> tuple:
        """Extract page and page_size from query."""
        import re
        
        page = 1
        page_size = None
        
        # Look for "page 2" or "page 3"
        page_match = re.search(r'page\s+(\d+)', query.lower())
        if page_match:
            page = int(page_match.group(1))
        
        # Look for "show 100" or "limit 50"
        size_match = re.search(r'(?:show|limit)\s+(\d+)', query.lower())
        if size_match:
            page_size = int(size_match.group(1))
        
        return page, page_size

    def _is_report_list_request(self, query: str) -> bool:
        """Check if user is asking for list of reports."""
        q = query.lower()
        patterns = [
            "list reports",
            "show reports",
            "available reports",
            "what reports",
            "report list"
        ]
        return any(pattern in q for pattern in patterns)

    def _list_reports(self, user_role: str = "user") -> Dict:
        """List all available reports for user's role."""
        categories = self.reporting_service.get_categories()
        message_lines = ["**📊 Available Reports**\n"]
        
        for category in categories:
            reports = self.reporting_service.list_reports(category=category, user_role=user_role)
            if reports:
                message_lines.append(f"\n**{category.title()} Reports:**")
                for report in reports:
                    access_badge = "🔒" if report.get("access_level") == "admin" else ""
                    message_lines.append(f"- {access_badge} **{report['name']}**: {report['description']}")
        
        message_lines.append("\n\n💡 **Tips:**")
        message_lines.append("- Ask for any report by name: *'show me the user performance report'*")
        message_lines.append("- Use pagination: *'show page 2'* or *'show 100 results'*")
        
        return {
            "messages": [AIMessage(content="\n".join(message_lines))],
            "report_result": None
        }

    def _match_report(self, query: str) -> str:
        """Match user query to a report ID."""
        q = query.lower()
        
        # Simple keyword matching
        for report_id, report_config in self.reporting_service.reports.items():
            report_name = report_config.get("name", "").lower()
            
            # Check if query contains report name keywords
            name_words = report_name.split()
            if all(word in q for word in name_words if len(word) > 3):
                return report_id
            
            # Check report ID
            if report_id.replace("_", " ") in q:
                return report_id
        
        return ""

    def _extract_filters(self, query: str) -> Dict[str, Any]:
        """Extract dynamic filters from user query using explicit filter syntax."""
        import re

        filters: Dict[str, Any] = {}
        q = str(query or "").strip()
        q_lower = q.lower()

        # Pattern: "for <Name>" (avoid broad tokens like "for report", "for summary")
        for_match = re.search(r"\bfor\s+([a-zA-Z][a-zA-Z-]{0,39})\b", q, re.IGNORECASE)
        if for_match:
            candidate = str(for_match.group(1) or "").strip()
            blocked = {
                "all",
                "everyone",
                "summary",
                "report",
                "reports",
                "today",
                "yesterday",
                "tomorrow",
            }
            if candidate and candidate.lower() not in blocked:
                filters["assignee"] = candidate.capitalize()

        # Pattern: "status: <value>" / "status is <value>" / "status=<value>"
        status_match = re.search(
            r"\bstatus\s*(?::|=|\bis\b|\bequals\b)\s*([a-zA-Z][a-zA-Z ]{0,30})\b",
            q_lower,
            re.IGNORECASE,
        )
        if status_match:
            raw_status = str(status_match.group(1) or "").strip().lower()
            if raw_status and raw_status not in {"summary", "report", "reports"}:
                filters["status"] = " ".join(part.capitalize() for part in raw_status.split())

        # Pattern: "priority: <number>" / "priority is <number>" / "priority=<number>"
        priority_match = re.search(
            r"\bpriority\s*(?::|=|\bis\b|\bequals\b)\s*(\d+)\b",
            q_lower,
            re.IGNORECASE,
        )
        if priority_match:
            filters["priority"] = int(priority_match.group(1))

        return filters

    def _format_report_results(
        self, 
        metadata: Dict, 
        results: list,
        page: int = 1,
        page_size: Optional[int] = None
    ) -> str:
        """Format report results for display."""
        if not results:
            return f"**📊 {metadata.get('name')}**\n\n📭 No data found matching your query."
        
        page_size = page_size or settings.DEFAULT_PAGE_SIZE
        
        lines = [f"**📊 {metadata.get('name')}**"]
        lines.append(f"*{metadata.get('description')}*\n")
        lines.append(f"**Results:** {len(results)} record(s) | **Page:** {page} | **Page Size:** {page_size}\n")
        
        # Ensure proper table rendering with blank line
        lines.append("")
        
        # Format as table
        headers = list(results[0].keys())
        lines.append(" | ".join(headers))
        lines.append(" | ".join(["---"] * len(headers)))
        
        for row in results:
            # Handle None values, format as string, and REMOVE NEWLINES to avoid breaking table
            values = []
            for h in headers:
                val = row.get(h)
                if val is None:
                    values.append("None")
                else:
                    # Replace newlines and tabs with spaces to keep table structure
                    s_val = str(val).replace("\n", " ").replace("\r", " ").replace("\t", " ").strip()
                    values.append(s_val)
            lines.append(" | ".join(values))
        
        lines.append(f"\n💡 *Use 'show page {page + 1}' for next page*")
        
        return "\n".join(lines)

    def _get_available_reports_message(self, user_role: str = "user") -> str:
        """Get message listing available reports."""
        categories = self.reporting_service.get_categories()
        lines = ["❓ I couldn't match your request to a specific report.\n"]
        lines.append("**Available report categories:**")
        
        for category in categories:
            count = len(self.reporting_service.list_reports(category=category, user_role=user_role))
            lines.append(f"- {category.title()}: {count} reports")
        
        lines.append("\n💡 Ask *'list reports'* to see all available reports.")
        return "\n".join(lines)
