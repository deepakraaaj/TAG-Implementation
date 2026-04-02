"""Dynamic reporting service for executing pre-defined reports."""
import json
import logging
import re
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from app.config import get_settings

settings = get_settings()
logger = logging.getLogger(__name__)


class ReportingService:
    """
    Production-ready reporting system with pagination, RBAC, and audit logging.
    
    Features:
    - Pagination support
    - Role-based access control
    - Query timeout configuration
    - Parameter validation
    """

    def __init__(self, domain_provider: Callable[[], Any]):
        self.domain_provider = domain_provider

    @property
    def domain(self):
        return self.domain_provider()

    @property
    def reports(self) -> Dict[str, Any]:
        return self._load_reports()

    def _load_reports(self) -> Dict[str, Any]:
        """Load report templates from domain configuration."""
        domain_path = Path(self.domain.domain_path)
        reports_file = domain_path / "reports.json"
        
        if not reports_file.exists():
            logger.warning(f"Reports file not found: {reports_file}")
            return {}
        
        try:
            with open(reports_file, 'r') as f:
                data = json.load(f)
                return data.get("reports", {})
        except Exception as e:
            logger.error(f"Failed to load reports: {e}")
            return {}

    def list_reports(
        self, 
        category: Optional[str] = None,
        user_role: str = "user"
    ) -> List[Dict[str, str]]:
        """
        List available reports filtered by category and user role.
        
        Args:
            category: Filter by category
            user_role: User's role (admin, user, public)
            
        Returns:
            List of report metadata
        """
        reports = []
        for report_id, report_config in self.reports.items():
            # Filter by category
            if category and report_config.get("category") != category:
                continue
            
            # Filter by access level
            access_level = report_config.get("access_level", "user")
            if not self._has_access(user_role, access_level):
                continue
            
            reports.append({
                "id": report_id,
                "name": report_config.get("name", report_id),
                "description": report_config.get("description", ""),
                "category": report_config.get("category", "other"),
                "access_level": access_level
            })
        
        return reports

    def get_report_query(
        self, 
        report_id: str, 
        params: Dict[str, Any],
        filters: Optional[Dict[str, Any]] = None,
        page: int = 1,
        page_size: Optional[int] = None
    ) -> Optional[str]:
        """
        Get SQL query for a report with parameters, dynamic filters and pagination.
        
        Args:
            report_id: Report identifier
            params: Parameters to substitute (company_id, user_id, etc.)
            filters: Dynamic filters from user query
            page: Page number (1-indexed)
            page_size: Number of rows per page
            
        Returns:
            SQL query string with parameters and pagination
        """
        if report_id not in self.reports:
            logger.error(f"Report not found: {report_id}")
            return None
        
        report_config = self.reports[report_id]
        query_template = report_config.get("query", "")
        
        if not query_template:
            logger.error(f"No query defined for report: {report_id}")
            return None
        
        # Substitute parameters
        try:
            query = query_template.format(**params)
        except KeyError as e:
            logger.error(f"Missing parameter for report {report_id}: {e}")
            return None

        # Add dynamic filters if provided
        if filters:
            where_parts = []
            for k, v in filters.items():
                if not k or v is None:
                    continue

                column = self._resolve_filter_column(query, str(k))
                if not column:
                    logger.debug("Skipping unsupported report filter key: %s", k)
                    continue

                # Basic SQL sanitization for value
                safe_v = str(v).replace("'", "''")

                # Check if it's a date filter or regular column
                if str(column).endswith("_date") and str(v).lower() == "today":
                    where_parts.append(f"DATE({column}) = CURDATE()")
                elif isinstance(v, (int, float)):
                    where_parts.append(f"{column} = {v}")
                else:
                    where_parts.append(f"{column} = '{safe_v}'")

            if where_parts:
                query = query.rstrip(";")
                query_upper = query.upper()
                
                # Find insertion point: before ORDER BY, GROUP BY, LIMIT
                insert_pos = len(query)
                for keyword in [" ORDER BY ", " GROUP BY ", " LIMIT "]:
                    idx = query_upper.rfind(keyword)
                    if idx != -1 and idx < insert_pos:
                        insert_pos = idx
                
                clause_prefix = " AND " if " WHERE " in query_upper[:insert_pos] else " WHERE "
                additional_where = clause_prefix + " AND ".join(where_parts)

                query = query[:insert_pos] + additional_where + query[insert_pos:]
        
        # Add pagination
        if page_size is None:
            page_size = settings.DEFAULT_PAGE_SIZE
        
        # Validate page size
        page_size = min(page_size, settings.MAX_PAGE_SIZE)
        page_size = max(page_size, 1)
        
        # Calculate offset
        offset = (page - 1) * page_size
        
        # Add LIMIT and OFFSET if not already present
        query_upper = query.upper()
        if "LIMIT" not in query_upper:
            query = query.rstrip(";") + f" LIMIT {page_size} OFFSET {offset};"
        
        return query

    @staticmethod
    def _safe_identifier(identifier: str) -> bool:
        return bool(
            re.fullmatch(
                r"[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)?",
                str(identifier or "").strip(),
            )
        )

    @staticmethod
    def _find_column_reference(query: str, column_name: str) -> Optional[str]:
        column = str(column_name or "").strip()
        if not column:
            return None

        alias_match = re.search(
            rf"\b([A-Za-z_][A-Za-z0-9_]*)\.{re.escape(column)}\b",
            query,
            re.IGNORECASE,
        )
        if alias_match:
            alias = str(alias_match.group(1)).strip()
            return f"{alias}.{column}"

        if re.search(rf"\b{re.escape(column)}\b", query, re.IGNORECASE):
            return column
        return None

    def _resolve_filter_column(self, query: str, requested_key: str) -> Optional[str]:
        requested = str(requested_key or "").strip()
        if not requested:
            return None

        logical_mappings: dict[str, list[str]] = {
            "assignee": ["first_name", "assignee_name", "assignee"],
            "status": ["status"],
            "priority": ["priority"],
            "assigned_user_id": ["assigned_user_id", "assignee_id"],
            "scheduled_date": ["scheduled_date"],
        }

        mapped_columns = logical_mappings.get(requested.lower(), [])
        for mapped_column in mapped_columns:
            resolved = self._find_column_reference(query, mapped_column)
            if resolved:
                return resolved

        if not self._safe_identifier(requested):
            return None

        if "." in requested and requested.upper() in query.upper():
            return requested

        column_only = requested.split(".", 1)[-1]
        return self._find_column_reference(query, column_only)

    def check_access(self, report_id: str, user_role: str) -> bool:
        """
        Check if user has access to a report.
        
        Args:
            report_id: Report identifier
            user_role: User's role (admin, user, public)
            
        Returns:
            True if user has access, False otherwise
        """
        if report_id not in self.reports:
            return False
        
        report_config = self.reports[report_id]
        access_level = report_config.get("access_level", "user")
        
        return self._has_access(user_role, access_level)

    def _has_access(self, user_role: str, required_level: str) -> bool:
        """Check if user role meets required access level."""
        role_hierarchy = {
            "public": 0,
            "user": 1,
            "admin": 2
        }
        
        user_level = role_hierarchy.get(user_role, 0)
        required = role_hierarchy.get(required_level, 1)
        
        return user_level >= required

    def get_report_metadata(self, report_id: str) -> Optional[Dict[str, Any]]:
        """Get metadata for a specific report."""
        if report_id not in self.reports:
            return None
        
        report_config = self.reports[report_id]
        return {
            "id": report_id,
            "name": report_config.get("name", report_id),
            "description": report_config.get("description", ""),
            "category": report_config.get("category", "other"),
            "access_level": report_config.get("access_level", "user"),
            "timeout_seconds": report_config.get("timeout_seconds", settings.QUERY_TIMEOUT_SECONDS),
            "max_rows": report_config.get("max_rows", settings.MAX_REPORT_ROWS),
            "parameters": self._extract_parameters(report_config.get("query", ""))
        }

    def _extract_parameters(self, query_template: str) -> List[str]:
        """Extract parameter names from query template."""
        params = re.findall(r'\{(\w+)\}', query_template)
        return list(set(params))

    def get_categories(self) -> List[str]:
        """Get list of all report categories."""
        categories = set()
        for report_config in self.reports.values():
            category = report_config.get("category", "other")
            categories.add(category)
        return sorted(list(categories))

    def get_timeout(self, report_id: str) -> int:
        """Get timeout for a specific report in seconds."""
        if report_id not in self.reports:
            return settings.QUERY_TIMEOUT_SECONDS
        
        report_config = self.reports[report_id]
        return report_config.get("timeout_seconds", settings.QUERY_TIMEOUT_SECONDS)
