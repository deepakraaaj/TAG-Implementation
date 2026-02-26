import asyncio

from app.assistant.nodes.sql_builder_node import SQLBuilderNode


class _AliasCatalog:
    @staticmethod
    def table_names():
        return {"asset"}

    @staticmethod
    def aliases(table):
        if str(table) == "asset":
            return ["asset", "assets"]
        return []

    @staticmethod
    def important_columns(_table):
        return {"id", "name", "company_id"}


class _AliasBuilder:
    def __init__(self):
        self.catalog = _AliasCatalog()

    @staticmethod
    def resolve_table(_query, _intent):
        return "asset"

    @staticmethod
    def parse_kv_pairs(_query):
        return {}

    async def build_select(self, _query, table, _company_id):
        return f"SELECT * FROM {table} LIMIT 100;"

    @staticmethod
    def build_select_from_filters(_table, _filters, _company_id):
        return "", "not used"


def test_sql_builder_node_canonicalizes_plural_table_name_from_intent():
    node = SQLBuilderNode()
    node.builder = _AliasBuilder()

    state = {
        "messages": [type("M", (), {"content": "list assets"})()],
        "metadata": {"company_id": "56942686"},
        "intent": {"operation": "select", "table": "assets", "fields": {}, "filters": {}},
    }

    result = asyncio.run(node.run(state))
    assert result["sql_query"] == "SELECT * FROM asset LIMIT 100;"


class _LegacyPersonCatalog:
    @staticmethod
    def table_names():
        return {"user"}

    @staticmethod
    def aliases(_table):
        return ["user"]

    @staticmethod
    def important_columns(_table):
        return {"id", "first_name", "last_name", "company_id"}


class _LegacyPersonBuilder:
    def __init__(self):
        self.catalog = _LegacyPersonCatalog()

    @staticmethod
    def resolve_table(_query, intent):
        return str((intent or {}).get("table", "") or "").strip()

    @staticmethod
    def parse_kv_pairs(_query):
        return {}

    async def build_select(self, _query, table, _company_id):
        return f"SELECT * FROM {table} LIMIT 100;"

    @staticmethod
    def build_select_from_filters(_table, _filters, _company_id):
        return "", "not used"


def test_sql_builder_node_maps_legacy_person_to_user_table():
    node = SQLBuilderNode()
    node.builder = _LegacyPersonBuilder()

    state = {
        "messages": [type("M", (), {"content": "list people"})()],
        "metadata": {"company_id": "56942686"},
        "intent": {"operation": "select", "table": "person", "fields": {}, "filters": {}},
    }

    result = asyncio.run(node.run(state))
    assert result["sql_query"] == "SELECT * FROM user LIMIT 100;"
