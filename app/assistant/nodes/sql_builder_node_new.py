from typing import Dict, Any
import re
import logging

from langchain_core.messages import AIMessage
import sqlglot
from sqlglot import exp

from app.assistant.services.sql_builder_service import SQLBuilderService
from app.assistant.services.prompt_injection_detector import PromptInjectionDetector
from app.assistant.services.intent_detection_service import IntentDetectionService

logger = logging.getLogger(__name__)


class SQLBuilderNode:
    def __init__(self):
        self.sql_builder = SQLBuilderService()
        self.injection_detector = PromptInjectionDetector()
        self.intent_detector = IntentDetectionService()

    @staticmethod
    def _is_unfiltered_select(sql: str) -> bool:
        text_sql = str(sql or "").strip()
        if not text_sql:
            return False
        try:
            parsed = sqlglot.parse_one(text_sql)
        except Exception:
            return False
        if not isinstance(parsed, exp.Select):
            return False
        return parsed.args.get("where") is None

    @staticmethod
    def _select_where_columns(sql: str) -> set[str]:
        text_sql = str(sql or "").strip()
        if not text_sql:
            return set()
        try:
            parsed = sqlglot.parse_one(text_sql)
        except Exception:
            return set()
        if not isinstance(parsed, exp.Select):
            return set()
        where_expr = parsed.args.get("where")
        if where_expr is None:
            return set()
        cols = set()
        for col in where_expr.find_all(exp.Column):
            name = str(col.name or "").strip().lower()
            if name:
                cols.add(name)
        return cols

    @staticmethod
    def _normalize_user_filters(user_input: str) -> Dict[str, str]:
        normalized = {}
        lowered = (user_input or "").lower()
        if "today" in lowered:
            normalized["scheduled_date"] = "CURDATE()"
        if "yesterday" in lowered:
            normalized["scheduled_date"] = "DATE_SUB(CURDATE(), INTERVAL 1 DAY)"
        if "this week" in lowered or "this_week" in lowered:
            normalized["scheduled_date"] = "WEEK"
        if "pending" in lowered:
            normalized.setdefault("status", "Pending")
        if "in progress" in lowered or "in-progress" in lowered:
            normalized.setdefault("status", "In Progress")
        if "completed" in lowered or "complete" in lowered:
            normalized.setdefault("status", "Completed")
        if "overdue" in lowered or "over due" in lowered:
            normalized.setdefault("status", "Overdue")
        return normalized

    def _generate_dynamic_filter_options(self, table: str) -> list[Dict[str, str]]:
        """Generate simplified question-based filter options (When, What, Who, Where)."""
        try:
            # Fix: Use correct attribute for catalog
            catalog = self.sql_builder.catalog
            columns = catalog.important_columns(table)
            
            if not columns:
                return [{"label": "Type your filters manually", "value": ""}]
            
            options = []
            seen_labels = set()
            
            # Define priority columns for labels to ensure we pick the best one
            # Maps Label -> list of column substrings to match, in order of preference
            label_preferences = {
                "Status": ["status", "state"],
                "Priority": ["priority"],
                "Date": ["scheduled", "due", "deadline", "date"],
                "Assigned To": ["assign", "assigned_to"],
                "People": ["user", "owner"],
                "Location": ["facility", "location", "site"],
                "Name": ["name", "title", "subject", "first_name", "username", "summary"],
                "Email": ["email"],
                "Reference": ["code", "number", "ref"],
            }
            
            used_columns = set()
            
            # We construct a tailored list
            for label, substrings in label_preferences.items():
                best_col = None
                for sub in substrings:
                    for col_name in columns.keys():
                        if col_name in used_columns:
                            continue

                        col_lower = col_name.lower()
                        # Skip technical fields
                        if col_lower in {
                            "id",
                            "company_id",
                            "date_updated",
                            "updated_by",
                            "created_by",
                            "date_created",
                            "active_date",
                            "active_time",
                            "closed_time",
                            "is_active",
                            "password",
                        }:
                            continue
                            
                        if sub in col_lower:
                            best_col = col_name
                            break
                    if best_col:
                        break
                
                if best_col and label not in seen_labels:
                    options.append({"label": label, "value": f"{best_col}="})
                    seen_labels.add(label)
                    used_columns.add(best_col)
            
            # Check for date shortcuts
            has_date = any("date" in c.lower() or "time" in c.lower() for c in columns.keys())
            if has_date:
                options.insert(0, {"label": "Yesterday", "value": "yesterday"})
                options.insert(0, {"label": "Today", "value": "today"})
            
            return options[:6] or [
                {"label": "Today", "value": "today"},
                {"label": "Yesterday", "value": "yesterday"},
            ]
            
        except Exception as e:
            logger.warning(f"Failed to generate dynamic filters for {table}: {e}")
            # Fallback to generic time-based options
            return [
                {"label": "Today", "value": "today"},
                {"label": "Yesterday", "value": "yesterday"},
            ]

    def _filter_prompt_payload(self, table: str, suggested_fields: list[str]) -> Dict:
        fields = [str(x).strip() for x in suggested_fields if str(x).strip()]
        if not fields:
            fields = ["id", "name", "date_created"]
        
        # Generate dynamic options based on table schema
        dynamic_options = self._generate_dynamic_filter_options(table)
        
        # Generate example based on table
        example = f"{fields[0] if fields else 'id'}=value"
        
        return {
            "workflow_id": "select_filters",
            "state": "collect_filters",
            "completed": False,
            "mode": "menu",
            "next_field": "filters",
            "collected_data": {
                "operation": "select",
                "table": table,
                "required_fields": ["filters"],
                "collected_fields": {},
            },
            "ui": {
                "type": "menu",
                "title": f"Add filters for {table}",
                "options": dynamic_options,
                "suggested_fields": fields[:6],
                "example": example,
            },
        }

    def _filter_prompt_message(self, table: str) -> str:
        """Generate filter prompt message with dynamic options."""
        dynamic_options = self._generate_dynamic_filter_options(table)
        
        lines = [f"Let me help you narrow down `{table}`."]
        lines.append("Pick an option number/value, or type filters directly. Use `back`/`cancel` anytime.")
        
        for idx, opt_dict in enumerate(dynamic_options, start=1):
            lines.append(f"{idx}. {opt_dict['label']}")
        
        # Generic example
        lines.append("Example: field_name=value, another_field=value")
        return "\n".join(lines)
