import asyncio

from app.assistant.nodes.sql.sql_builder_node import SQLBuilderNode
from app.domains.registry import DomainRegistry


class _SpecialQueryCatalog:
    @staticmethod
    def table_names():
        return {"vehicle", "vts_exception", "trip", "location"}

    @staticmethod
    def important_columns(table):
        if table == "vehicle":
            return {"id", "vehicle_number"}
        if table == "vts_exception":
            return {"vehicle_id", "company_id", "is_over_speed", "over_speed_count"}
        if table == "trip":
            return {"id", "company_id", "location_id"}
        if table == "location":
            return {"id", "name", "location_code"}
        return set()

    @staticmethod
    def table_meta(table):
        if table == "vts_exception":
            return {
                "important_columns": {
                    "vehicle_id": {},
                    "company_id": {},
                    "is_over_speed": {},
                    "over_speed_count": {},
                },
                "tenant_scope": {"column": "company_id", "metadata_key": "company_id"},
            }
        if table == "vehicle":
            return {"important_columns": {"id": {}, "vehicle_number": {}}}
        if table == "trip":
            return {
                "important_columns": {
                    "id": {},
                    "company_id": {},
                    "location_id": {},
                },
                "tenant_scope": {"column": "company_id", "metadata_key": "company_id"},
            }
        if table == "location":
            return {"important_columns": {"id": {}, "name": {}, "location_code": {}}}
        return {}


class _SpecialQueryBuilder:
    def __init__(self):
        self.catalog = _SpecialQueryCatalog()

    @staticmethod
    def resolve_table(_query, _intent):
        return ""

    @staticmethod
    def parse_kv_pairs(_query):
        return {}


class _SpecialQueryIntentDetector:
    async def detect_intent(self, _query, _metadata):
        return {"operation": "SELECT", "table": "", "filters": []}


def _msg(text: str):
    return type("M", (), {"content": text})()


def test_vts_special_query_comes_from_domain_config_for_overspeed_ranking():
    node = SQLBuilderNode(sql_builder=_SpecialQueryBuilder(), intent_detector=_SpecialQueryIntentDetector())
    state = {
        "messages": [_msg("which truck reported as many times overspeeded")],
        "metadata": {"company_id": "56942673"},
        "intent": {},
    }

    with DomainRegistry.use_domain("vts"):
        result = asyncio.run(node.run(state))

    sql = result["sql_query"]
    assert "FROM vts_exception ve" in sql
    assert "JOIN vehicle v ON ve.vehicle_id = v.id" in sql
    assert "SUM(COALESCE(ve.over_speed_count, 0)) AS total_overspeed_events" in sql
    assert "COUNT(*) AS exception_records" in sql
    assert "ve.company_id = 56942673" in sql
    assert "LIMIT 1;" in sql


def test_vts_special_query_uses_plural_limit_from_domain_config():
    node = SQLBuilderNode(sql_builder=_SpecialQueryBuilder(), intent_detector=_SpecialQueryIntentDetector())
    state = {
        "messages": [_msg("show top overspeeded trucks")],
        "metadata": {"company_id": "56942673"},
        "intent": {},
    }

    with DomainRegistry.use_domain("vts"):
        result = asyncio.run(node.run(state))

    sql = result["sql_query"]
    assert "ORDER BY total_overspeed_events DESC, exception_records DESC" in sql
    assert "LIMIT 10;" in sql


def test_vts_special_query_ranks_locations_by_trip_count():
    node = SQLBuilderNode(sql_builder=_SpecialQueryBuilder(), intent_detector=_SpecialQueryIntentDetector())
    state = {
        "messages": [_msg("which location has the most trips")],
        "metadata": {"company_id": "56942673"},
        "intent": {},
    }

    with DomainRegistry.use_domain("vts"):
        result = asyncio.run(node.run(state))

    sql = result["sql_query"]
    assert "FROM trip t LEFT JOIN location l ON t.location_id = l.id" in sql
    assert "t.company_id = 56942673" in sql
    assert "COUNT(*) AS trip_count" in sql
    assert "ORDER BY trip_count DESC, location_name ASC" in sql
    assert "LIMIT 1;" in sql


def test_vts_special_query_show_locations_still_uses_locations_list():
    node = SQLBuilderNode(sql_builder=_SpecialQueryBuilder(), intent_detector=_SpecialQueryIntentDetector())
    state = {
        "messages": [_msg("show locations")],
        "metadata": {"company_id": "56942673"},
        "intent": {},
    }

    with DomainRegistry.use_domain("vts"):
        result = asyncio.run(node.run(state))

    sql = result["sql_query"]
    assert "FROM location l" in sql
    assert "WHERE l.is_active = 1" in sql
    assert "COUNT(*) AS trip_count" not in sql
