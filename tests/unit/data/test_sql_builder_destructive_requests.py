import asyncio

from app.assistant.nodes.sql.sql_builder_node import SQLBuilderNode


class _DeleteCatalog:
    @staticmethod
    def table_names():
        return {"facility"}

    @staticmethod
    def aliases(table):
        if str(table) == "facility":
            return ["facility", "facilities", "site", "sites"]
        return []

    @staticmethod
    def important_columns(_table):
        return {"id", "name", "company_id"}


class _DeleteBuilder:
    def __init__(self):
        self.catalog = _DeleteCatalog()

    @staticmethod
    def resolve_table(_query, _intent):
        return ""

    @staticmethod
    def parse_kv_pairs(_query):
        return {}


class _DeleteIntentDetector:
    @staticmethod
    def fallback_intent(_query):
        return {}


def test_sql_builder_node_rejects_broad_delete_request_with_explicit_message():
    node = SQLBuilderNode(sql_builder=_DeleteBuilder(), intent_detector=_DeleteIntentDetector())
    state = {
        "messages": [type("M", (), {"content": "delete db"})()],
        "metadata": {"company_id": "56942686"},
        "intent": {"operation": "select", "table": "", "fields": {}, "filters": {}},
    }

    result = asyncio.run(node.run(state))

    assert result["sql_query"] == "SKIP"
    assert str(result["messages"][0].content) == "I can't delete the database. Destructive delete/drop requests are blocked."


def test_sql_builder_node_uses_recent_delete_context_for_entity_followup_with_typo():
    node = SQLBuilderNode(sql_builder=_DeleteBuilder(), intent_detector=_DeleteIntentDetector())
    state = {
        "messages": [type("M", (), {"content": "facilty table"})()],
        "metadata": {
            "company_id": "56942686",
            "_recent_conversation": [
                {"role": "user", "content": "delete db"},
                {
                    "role": "assistant",
                    "content": "I can't delete the database. Destructive delete/drop requests are blocked.",
                },
            ],
        },
        "intent": {"operation": "select", "table": "", "fields": {}, "filters": {}},
    }

    result = asyncio.run(node.run(state))

    assert result["sql_query"] == "SKIP"
    assert str(result["messages"][0].content) == (
        "I can't delete records from `facility`. "
        "Destructive delete requests are blocked. I can help you review records or perform supported updates instead."
    )
