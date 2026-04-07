import asyncio

from app.assistant.nodes.sql.sql_builder_node import SQLBuilderNode
from app.domains.registry import DomainRegistry


class _SpecialQueryCatalog:
    @staticmethod
    def table_names():
        return {"vehicle", "vts_exception"}

    @staticmethod
    def important_columns(table):
        if table == "vehicle":
            return {"id", "vehicle_number"}
        if table == "vts_exception":
            return {"vehicle_id", "company_id", "is_over_speed", "over_speed_count"}
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
    assert "SUM(COALESCE(ve.over_speed_count, 0)) AS overspeed_count" in sql
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
    assert "ORDER BY overspeed_count DESC, overspeed_rows DESC" in sql
    assert "LIMIT 10;" in sql
