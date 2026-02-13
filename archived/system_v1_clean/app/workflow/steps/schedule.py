from __future__ import annotations

import re
import logging
from typing import Any, Dict, List, Optional

from sqlalchemy import text

from app.services.cache import cache
from app.workflow.engine.dsl import WorkflowStateDefinition
from app.workflow.engine.registry import runtime_registry
from app.workflow.engine.types import MenuResolverResult, ValidationResult, WorkflowContext
from app.workflow.engine.ui import WorkflowMenuItem, WorkflowPagination

logger = logging.getLogger(__name__)

CACHE_USER_ID_KEY = "user_id"

def _coerce_int(value: Any) -> Optional[int]:
    if value is None:
        return None
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def _clean_search_term(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    cleaned = value.strip()
    return cleaned if cleaned else None


def _like_pattern(term: str) -> str:
    escaped = term.lower().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def _normalize_slot_time(raw: Optional[str]) -> Optional[str]:
    """Return a consistently formatted 12-hour time label when possible."""
    if not raw:
        return None
    cleaned = re.sub(r"\s+", " ", raw.strip())
    cleaned = cleaned.replace(".", ":")
    match = re.match(r"(?i)^(\d{1,2}):(\d{2})\s*(am|pm)?$", cleaned)
    if not match:
        return cleaned
    hour = int(match.group(1))
    minute = int(match.group(2))
    meridiem = match.group(3)
    if meridiem:
        return f"{hour}:{minute:02d} {meridiem.upper()}"
    return f"{hour}:{minute:02d}"


def _format_duration(hours: Any, minutes: Any) -> Optional[str]:
    hour_val = _coerce_int(hours)
    minute_val = _coerce_int(minutes)
    parts: List[str] = []
    if hour_val:
        parts.append(f"{hour_val}h")
    if minute_val:
        parts.append(f"{minute_val}m")
    if not parts:
        return None
    return " ".join(parts)


def _friendly_reference(value: Any) -> str:
    if value is None:
        return ""
    text_value = str(value)
    if len(text_value) <= 10:
        return text_value
    return f"{text_value[:4]}…{text_value[-4:]}"


class ScheduleDataResolver:
    def __init__(self, services: Dict[str, Any]) -> None:
        self.schema_service = services.get("schema_service")
        if not self.schema_service:
            # Fallback to creating a new SchemaService if not provided, though not ideal
            from app.services.schema_service import SchemaService
            self.schema_service = SchemaService()

    async def _execute_query(self, query: str, params: Dict[str, Any] = None):
        # Use default engine from schema service
        engine = self.schema_service.get_engine_for_url(None)
        with engine.connect() as conn:
            result = conn.execute(text(query), params or {})
            return result.mappings().all()
            
    async def _execute_scalar_one(self, query: str, params: Dict[str, Any] = None):
        engine = self.schema_service.get_engine_for_url(None)
        with engine.connect() as conn:
             result = conn.execute(text(query), params or {})
             try:
                return result.mappings().first()
             except Exception:
                return None

    async def _execute_write(self, query: str, params: Dict[str, Any] = None):
        engine = self.schema_service.get_engine_for_url(None)
        with engine.begin() as conn: # Transaction
            conn.execute(text(query), params or {})

    async def fetch_user_company(self, user_id: int) -> Optional[int]:
        stmt = "SELECT company_id FROM user WHERE id = :user_id AND is_active = 1 LIMIT 1"
        row = await self._execute_scalar_one(stmt, {"user_id": user_id})
        if not row:
            return None
        company_id = row.get("company_id")
        return int(company_id) if company_id is not None else None

    async def fetch_scheduler_refs(
        self, company_id: int, limit: int, offset: int, search: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        params: Dict[str, Any] = {"company_id": company_id, "limit": limit, "offset": offset}
        search_term = _clean_search_term(search)
        if search_term:
            params["pattern"] = _like_pattern(search_term)
            stmt = (
                "SELECT scheduled_ref_no, id, name, hours, minutes "
                "FROM scheduler_details "
                "WHERE company_id = :company_id AND is_active = 1 "
                "AND (LOWER(name) LIKE :pattern ESCAPE '\\\\' OR LOWER(scheduled_ref_no) LIKE :pattern ESCAPE '\\\\') "
                "ORDER BY name ASC LIMIT :limit OFFSET :offset"
            )
        else:
            stmt = (
                "SELECT scheduled_ref_no, id, name, hours, minutes "
                "FROM scheduler_details "
                "WHERE company_id = :company_id AND is_active = 1 "
                "ORDER BY date DESC, id DESC "
                "LIMIT :limit OFFSET :offset"
            )
        rows = await self._execute_query(stmt, params)
        return [dict(row) for row in rows]

    async def fetch_facilities(
        self, company_id: int, limit: int, offset: int, search: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        params: Dict[str, Any] = {"company_id": company_id, "limit": limit, "offset": offset}
        search_term = _clean_search_term(search)
        if search_term:
            params["pattern"] = _like_pattern(search_term)
            stmt = (
                "SELECT id, name FROM facility WHERE company_id = :company_id AND is_active = 1 "
                "AND LOWER(name) LIKE :pattern ESCAPE '\\\\' "
                "ORDER BY name ASC LIMIT :limit OFFSET :offset"
            )
        else:
            stmt = (
                "SELECT id, name FROM facility WHERE company_id = :company_id AND is_active = 1 "
                "ORDER BY id DESC LIMIT :limit OFFSET :offset"
            )
        rows = await self._execute_query(stmt, params)
        return [dict(row) for row in rows]

    async def fetch_users(
        self, company_id: int, limit: int, offset: int, search: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        params: Dict[str, Any] = {"company_id": company_id, "limit": limit, "offset": offset}
        search_term = _clean_search_term(search)
        if search_term:
            params["pattern"] = _like_pattern(search_term)
            stmt = (
                "SELECT id, CONCAT(first_name, ' ', last_name) AS name "
                "FROM user "
                "WHERE company_id = :company_id AND is_active = 1 "
                "AND LOWER(CONCAT(first_name, ' ', last_name)) LIKE :pattern ESCAPE '\\\\' "
                "ORDER BY name ASC LIMIT :limit OFFSET :offset"
            )
        else:
            stmt = (
                "SELECT id, CONCAT(first_name, ' ', last_name) AS name "
                "FROM user "
                "WHERE company_id = :company_id AND is_active = 1 "
                "ORDER BY id DESC LIMIT :limit OFFSET :offset"
            )
        rows = await self._execute_query(stmt, params)
        return [dict(row) for row in rows]

    async def count_scheduler_refs(self, company_id: int) -> int:
        stmt = "SELECT COUNT(*) AS total FROM scheduler_details WHERE company_id = :company_id AND is_active = 1"
        row = await self._execute_scalar_one(stmt, {"company_id": company_id})
        return int(row["total"]) if row and row.get("total") is not None else 0

    async def count_facilities(self, company_id: int) -> int:
        stmt = "SELECT COUNT(*) AS total FROM facility WHERE company_id = :company_id AND is_active = 1"
        row = await self._execute_scalar_one(stmt, {"company_id": company_id})
        return int(row["total"]) if row and row.get("total") is not None else 0

    async def count_users(self, company_id: int) -> int:
        stmt = "SELECT COUNT(*) AS total FROM user WHERE company_id = :company_id AND is_active = 1"
        row = await self._execute_scalar_one(stmt, {"company_id": company_id})
        return int(row["total"]) if row and row.get("total") is not None else 0

    async def count_tasks(self, company_id: int, facility_id: Optional[int]) -> int:
        if facility_id:
            stmt = (
                "SELECT COUNT(DISTINCT td.id) AS total "
                "FROM scheduler_task_details std "
                "JOIN task_description td ON td.id = std.task_description_id "
                "WHERE std.facility_id = :facility_id AND std.is_active = 1 AND td.is_active = 1"
            )
            row = await self._execute_scalar_one(stmt, {"facility_id": facility_id})
            if row and row.get("total") is not None:
                return int(row["total"])
        stmt = "SELECT COUNT(*) AS total FROM task_description WHERE company_id = :company_id AND is_active = 1"
        row = await self._execute_scalar_one(stmt, {"company_id": company_id})
        return int(row["total"]) if row and row.get("total") is not None else 0

    async def fetch_tasks(
        self,
        company_id: int,
        facility_id: Optional[int],
        limit: int,
        offset: int,
        search: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        params: Dict[str, Any] = {"limit": limit, "offset": offset}
        search_term = _clean_search_term(search)
        if facility_id:
            params["facility_id"] = facility_id
            query = (
                "SELECT DISTINCT td.id, td.name "
                "FROM scheduler_task_details std "
                "JOIN task_description td ON td.id = std.task_description_id "
                "WHERE std.facility_id = :facility_id AND std.is_active = 1 AND td.is_active = 1 "
            )
            if search_term:
                params["pattern"] = _like_pattern(search_term)
                query += "AND LOWER(td.name) LIKE :pattern ESCAPE '\\\\' "
                order_by = "ORDER BY td.name ASC "
            else:
                order_by = "ORDER BY td.id DESC "
            query += f"{order_by}LIMIT :limit OFFSET :offset"
            rows = await self._execute_query(query, params)
            if rows:
                return [dict(row) for row in rows]
        params = {"company_id": company_id, "limit": limit, "offset": offset}
        if search_term:
            params["pattern"] = _like_pattern(search_term)
            stmt = (
                "SELECT id, name FROM task_description WHERE company_id = :company_id AND is_active = 1 "
                "AND LOWER(name) LIKE :pattern ESCAPE '\\\\' "
                "ORDER BY name ASC LIMIT :limit OFFSET :offset"
            )
        else:
            stmt = (
                "SELECT id, name FROM task_description WHERE company_id = :company_id AND is_active = 1 "
                "ORDER BY id DESC LIMIT :limit OFFSET :offset"
            )
        rows = await self._execute_query(stmt, params)
        return [dict(row) for row in rows]

    async def facility_exists(self, facility_id: int, company_id: int) -> bool:
        stmt = "SELECT id FROM facility WHERE id = :facility_id AND company_id = :company_id AND is_active = 1 LIMIT 1"
        row = await self._execute_scalar_one(stmt, {"facility_id": facility_id, "company_id": company_id})
        return row is not None

    async def fetch_scheduler_detail_id(self, scheduled_ref_no: str) -> Optional[int]:
        stmt = "SELECT id FROM scheduler_details WHERE scheduled_ref_no = :scheduled_ref_no AND is_active = 1 LIMIT 1"
        row = await self._execute_scalar_one(stmt, {"scheduled_ref_no": scheduled_ref_no})
        if not row:
            return None
        detail_id = row.get("id")
        return int(detail_id) if detail_id is not None else None

    async def asset_exists(self, asset_id: int, company_id: int) -> bool:
        stmt = "SELECT id FROM asset WHERE id = :asset_id AND company_id = :company_id AND is_active = 1 LIMIT 1"
        row = await self._execute_scalar_one(stmt, {"asset_id": asset_id, "company_id": company_id})
        return row is not None


async def _ensure_company_id(context: WorkflowContext) -> Optional[int]:
    cached = context.collected_data.get("company_id")
    if cached:
        return _coerce_int(cached)
        
    cache_entry = await cache.get(context.session_id) or {}
    
    cache_user_id = cache_entry.get(CACHE_USER_ID_KEY)
    if cache_user_id is None:
        profile = cache_entry.get("user_profile") or {}
        cache_user_id = profile.get("user_id")
        
    if cache_user_id is not None and str(cache_user_id) == str(context.user_id):
        cached_company = cache_entry.get("company_id")
        if cached_company is None:
            cached_company = (cache_entry.get("user_profile") or {}).get("company_id")
        company_id = _coerce_int(cached_company)
        if company_id:
            context.collected_data["company_id"] = str(company_id)
            return company_id

    requester_id = _coerce_int(context.user_id)
    if requester_id is None:
        return None
    resolver = ScheduleDataResolver(context.services)
    company_id = await resolver.fetch_user_company(requester_id)
    if company_id is not None:
        context.collected_data["company_id"] = str(company_id)
        cache_entry = await cache.get(context.session_id) or {}
        cache_entry.update({"company_id": company_id, CACHE_USER_ID_KEY: str(context.user_id)})
        await cache.set(context.session_id, cache_entry, ttl=3600)
    return company_id


async def ensure_company_context(
    context: WorkflowContext, state: WorkflowStateDefinition
) -> str:
    company_id = await _ensure_company_id(context)
    if company_id is None:
        return "Unable to determine company scope. Provide company_id explicitly."
    return f"Using company_id {company_id}."


async def _menu_result_from_rows(
    rows: List[Dict[str, Any]],
    page: int,
    page_size: int,
    label_builder,
) -> MenuResolverResult:
    items: List[WorkflowMenuItem] = []
    for row in rows:
        value, label = label_builder(row)
        metadata = {}
        if "name" in row and row.get("name"):
            metadata["name"] = row["name"]
        items.append(WorkflowMenuItem(id=str(value), label=str(label), metadata=metadata or None))
    return MenuResolverResult(
        items=items,
        pagination=WorkflowPagination(
            page=page, page_size=page_size, has_more=len(rows) == page_size
        ),
    )


async def list_scheduler_refs(
    context: WorkflowContext,
    state: WorkflowStateDefinition,
    page: int,
    page_size: int,
    search: Optional[str] = None,
) -> MenuResolverResult:
    company_id = await _ensure_company_id(context)
    if company_id is None:
        return MenuResolverResult(
            items=[],
            pagination=WorkflowPagination(page=1, page_size=page_size, has_more=False),
            title="Provide company_id",
            description="I could not determine the company. Please supply company_id first.",
        )
    resolver = ScheduleDataResolver(context.services)
    rows = await resolver.fetch_scheduler_refs(company_id, page_size, (page - 1) * page_size, search)
    items: List[WorkflowMenuItem] = []
    for row in rows:
        slot_id = row.get("id")
        slot_time = _normalize_slot_time(row.get("name"))
        reference = row.get("scheduled_ref_no")
        friendly_ref = _friendly_reference(reference)
        primary_parts: List[str] = []
        if slot_time:
            primary_parts.append(slot_time)
        duration = _format_duration(row.get("hours"), row.get("minutes"))
        if duration:
            primary_parts.append(duration)
        label = " • ".join(primary_parts) if primary_parts else f"Schedule {friendly_ref}"
        description_parts = []
        if friendly_ref:
            description_parts.append(f"Reference {friendly_ref}")
        if slot_id is not None:
            description_parts.append(f"Slot #{slot_id}")
        items.append(
            WorkflowMenuItem(
                id=str(row["scheduled_ref_no"]),
                label=label,
                description=" | ".join(description_parts) if description_parts else "",
                metadata={
                    "slot_id": slot_id,
                    "reference": reference,
                },
            )
        )
    return MenuResolverResult(
        items=items,
        pagination=WorkflowPagination(page=page, page_size=page_size, has_more=len(rows) == page_size),
    )


async def count_scheduler_refs(
    context: WorkflowContext,
    state: WorkflowStateDefinition,
) -> Optional[int]:
    company_id = await _ensure_company_id(context)
    if company_id is None:
        return None
    resolver = ScheduleDataResolver(context.services)
    return await resolver.count_scheduler_refs(company_id)


async def list_facilities(
    context: WorkflowContext,
    state: WorkflowStateDefinition,
    page: int,
    page_size: int,
    search: Optional[str] = None,
) -> MenuResolverResult:
    company_id = await _ensure_company_id(context)
    if company_id is None:
        return MenuResolverResult(
            items=[],
            pagination=WorkflowPagination(page=1, page_size=page_size),
            title="Provide company_id",
            description="Unable to scope facilities without company_id.",
        )
    resolver = ScheduleDataResolver(context.services)
    rows = await resolver.fetch_facilities(company_id, page_size, (page - 1) * page_size, search)
    return await _menu_result_from_rows(
        rows,
        page,
        page_size,
        lambda row: (row["id"], f"{row['id']} ({row.get('name')})"),
    )


async def count_facilities(
    context: WorkflowContext,
    state: WorkflowStateDefinition,
) -> Optional[int]:
    company_id = await _ensure_company_id(context)
    if company_id is None:
        return None
    resolver = ScheduleDataResolver(context.services)
    return await resolver.count_facilities(company_id)


async def list_tasks(
    context: WorkflowContext,
    state: WorkflowStateDefinition,
    page: int,
    page_size: int,
    search: Optional[str] = None,
) -> MenuResolverResult:
    company_id = await _ensure_company_id(context)
    facility_id = _coerce_int(context.collected_data.get("facility_id"))
    if company_id is None:
        return MenuResolverResult(
            items=[],
            pagination=WorkflowPagination(page=1, page_size=page_size),
            title="Provide company_id",
            description="Unable to load tasks without company_id.",
        )
    resolver = ScheduleDataResolver(context.services)
    rows = await resolver.fetch_tasks(
        company_id, facility_id, page_size, (page - 1) * page_size, search
    )
    return await _menu_result_from_rows(
        rows,
        page,
        page_size,
        lambda row: (row["id"], f"{row['id']} ({row.get('name')})"),
    )


async def count_tasks(
    context: WorkflowContext,
    state: WorkflowStateDefinition,
) -> Optional[int]:
    company_id = await _ensure_company_id(context)
    facility_id = _coerce_int(context.collected_data.get("facility_id"))
    if company_id is None:
        return None
    resolver = ScheduleDataResolver(context.services)
    return await resolver.count_tasks(company_id, facility_id)


async def list_users(
    context: WorkflowContext,
    state: WorkflowStateDefinition,
    page: int,
    page_size: int,
    search: Optional[str] = None,
) -> MenuResolverResult:
    company_id = await _ensure_company_id(context)
    if company_id is None:
        return MenuResolverResult(
            items=[],
            pagination=WorkflowPagination(page=1, page_size=page_size),
            title="Provide company_id",
            description="Unable to load users without company_id.",
        )
    resolver = ScheduleDataResolver(context.services)
    rows = await resolver.fetch_users(company_id, page_size, (page - 1) * page_size, search)
    return await _menu_result_from_rows(
        rows,
        page,
        page_size,
        lambda row: (row["id"], f"{row['id']} ({row.get('name')})"),
    )


async def count_users(
    context: WorkflowContext,
    state: WorkflowStateDefinition,
) -> Optional[int]:
    company_id = await _ensure_company_id(context)
    if company_id is None:
        return None
    resolver = ScheduleDataResolver(context.services)
    return await resolver.count_users(company_id)


async def validate_facility(
    context: WorkflowContext, state: WorkflowStateDefinition, selection_id: str
) -> ValidationResult:
    company_id = await _ensure_company_id(context)
    if company_id is None:
        return ValidationResult(
            valid=False, message="Provide company_id before selecting a facility."
        )
    resolver = ScheduleDataResolver(context.services)
    facility_id = _coerce_int(selection_id)
    if facility_id is None:
        return ValidationResult(valid=False, message="Facility id must be numeric.")
    exists = await resolver.facility_exists(facility_id, company_id)
    if not exists:
        return ValidationResult(
            valid=False, message="Facility does not belong to your company."
        )
    return ValidationResult(valid=True)


async def validate_duration(
    context: WorkflowContext, state: WorkflowStateDefinition, value: str
) -> ValidationResult:
    minutes = _coerce_int(value)
    if not minutes or minutes <= 0:
        return ValidationResult(
            valid=False, message="Enter the estimated minutes as a positive integer."
        )
    return ValidationResult(valid=True)


async def create_schedule_record(
    context: WorkflowContext, state: WorkflowStateDefinition
) -> str:
    resolver = ScheduleDataResolver(context.services)
    company_id = await _ensure_company_id(context)
    if company_id is None:
        return "Company id is required before creating a schedule."
    facility_id = _coerce_int(context.collected_data.get("facility_id"))
    assignee_id = _coerce_int(
        context.collected_data.get("assignee_user_id") or context.user_id
    )
    minutes = _coerce_int(context.collected_data.get("task_est_time"))
    scheduled_ref = context.collected_data.get("scheduled_ref_no")
    task_description_id = _coerce_int(
        context.collected_data.get("task_description_id")
        or context.collected_data.get("task_description")
    )
    asset_id = _coerce_int(context.collected_data.get("asset_id"))
    errors = []
    if not facility_id:
        errors.append("facility_id is required.")
    if not scheduled_ref:
        errors.append("scheduled_ref_no is required.")
    if not task_description_id:
        errors.append("task description is required.")
    if not minutes:
        errors.append("task_est_time is required.")
    if errors:
        return "Cannot write schedule:\n" + "\n".join(f"- {msg}" for msg in errors)

    sche_details_id = await resolver.fetch_scheduler_detail_id(scheduled_ref)
    if sche_details_id is None:
        return "scheduled_ref_no is not valid."
    if asset_id is not None and not await resolver.asset_exists(asset_id, company_id):
        return f"Asset id {asset_id} does not belong to this company."

    insert_sql = (
        "INSERT INTO scheduler_task_details ("
        "scheduled_ref_no, company_id, facility_id, asset_id, task_description_id, "
        "user_id, task_est_time, sche_details_id, is_active, date_created"
        ") VALUES ("
        ":scheduled_ref_no, :company_id, :facility_id, :asset_id, :task_description_id, "
        ":user_id, :task_est_time, :sche_details_id, 1, NOW())"
    )
    params = {
        "scheduled_ref_no": scheduled_ref,
        "company_id": company_id,
        "facility_id": facility_id,
        "asset_id": asset_id,
        "task_description_id": task_description_id,
        "user_id": assignee_id,
        "task_est_time": minutes,
        "sche_details_id": sche_details_id,
    }
    try:
        await resolver._execute_write(insert_sql, params)
    except Exception as exc:  # noqa: BLE001
        logger.exception("Failed to insert scheduler_task_details")
        return f"Failed to create schedule: {exc}"
    summary = [
        "Scheduled task created:",
        f"- scheduled_ref_no: {scheduled_ref}",
        f"- facility_id: {facility_id}",
        f"- task_description_id: {task_description_id}",
        f"- task_est_time: {minutes} minutes",
        f"- assignee user_id: {assignee_id}",
    ]
    if asset_id:
        summary.append(f"- asset_id: {asset_id}")
    return "\n".join(summary)


runtime_registry.register_action(
    "schedule.ensure_company_context", ensure_company_context
)
runtime_registry.register_resolver("schedule.list_scheduler_refs", list_scheduler_refs)
runtime_registry.register_resolver("schedule.list_facilities", list_facilities)
runtime_registry.register_resolver("schedule.list_tasks", list_tasks)
runtime_registry.register_resolver("schedule.list_users", list_users)
runtime_registry.register_count_resolver(
    "schedule.count_scheduler_refs", count_scheduler_refs
)
runtime_registry.register_count_resolver("schedule.count_facilities", count_facilities)
runtime_registry.register_count_resolver("schedule.count_tasks", count_tasks)
runtime_registry.register_count_resolver("schedule.count_users", count_users)
runtime_registry.register_validator("schedule.validate_facility", validate_facility)
runtime_registry.register_validator("schedule.validate_duration", validate_duration)
runtime_registry.register_action(
    "schedule.create_schedule_record", create_schedule_record
)
