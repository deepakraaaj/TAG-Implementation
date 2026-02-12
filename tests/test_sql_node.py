import unittest

from app.services.schema_manifest_service import SchemaManifestService
from app.workflow.nodes.sql_node import _maybe_build_asset_create_sql, _maybe_build_user_query_sql


class TestSQLNodeFastPath(unittest.TestCase):
    def setUp(self):
        self.schema_manifest = SchemaManifestService()

    def test_user_count_query(self):
        sql = _maybe_build_user_query_sql(
            "how many users are there",
            {"intent_type": "aggregation", "entities": ["user"]},
            56942686,
            self.schema_manifest,
        )
        self.assertIn("COUNT(*) AS total_users", sql)
        self.assertIn("FROM user", sql)
        self.assertIn("company_id = 56942686", sql)

    def test_user_list_query(self):
        sql = _maybe_build_user_query_sql(
            "list users",
            {"intent_type": "listing", "entities": ["user"]},
            56942686,
            self.schema_manifest,
        )
        self.assertIn("COUNT(*) OVER() AS _total_count", sql)
        self.assertIn("FROM user", sql)
        self.assertIn("ORDER BY user.id DESC LIMIT 500", sql)
        self.assertNotIn("JOIN", sql.upper())

    def test_non_user_query_skips_fast_path(self):
        sql = _maybe_build_user_query_sql(
            "list facilities",
            {"intent_type": "listing", "entities": ["facility"]},
            56942686,
            self.schema_manifest,
        )
        self.assertEqual(sql, "")

    def test_alias_resolution_from_query(self):
        sql = _maybe_build_user_query_sql(
            "show staff",
            {"intent_type": "listing", "entities": []},
            56942686,
            self.schema_manifest,
        )
        self.assertIn("FROM user", sql)

    def test_unknown_intent_skips_fast_path(self):
        sql = _maybe_build_user_query_sql(
            "users",
            {"intent_type": "unknown", "entities": ["user"]},
            56942686,
            self.schema_manifest,
        )
        self.assertEqual(sql, "")

    def test_asset_create_with_category_name(self):
        sql = _maybe_build_asset_create_sql(
            "asset name : Water bottle category : Furnitures & seatings",
            {"intent_type": "mutation", "entities": ["asset"]},
            56942686,
            self.schema_manifest,
        )
        self.assertIn("INSERT INTO asset", sql)
        self.assertIn("'Water bottle'", sql)
        self.assertIn("FROM asset_category", sql)
        self.assertIn("Furnitures & seatings", sql)

    def test_asset_create_with_category_id(self):
        sql = _maybe_build_asset_create_sql(
            "create asset name: Water bottle category id: 12",
            {"intent_type": "mutation", "entities": ["asset"]},
            56942686,
            self.schema_manifest,
        )
        self.assertIn("INSERT INTO asset", sql)
        self.assertIn("asset_category_id", sql)
        self.assertIn(", 12,", sql)

    def test_asset_create_skips_without_required_fields(self):
        sql = _maybe_build_asset_create_sql(
            "create a asset",
            {"intent_type": "mutation", "entities": ["asset"]},
            56942686,
            self.schema_manifest,
        )
        self.assertEqual(sql, "")


if __name__ == "__main__":
    unittest.main()
