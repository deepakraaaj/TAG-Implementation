import unittest

from app.services.schema_manifest_service import SchemaManifestService
from app.workflow.nodes.sql_node import (
    _merge_intent_with_understanding,
    _maybe_build_asset_create_sql,
    _maybe_build_asset_query_sql,
    _maybe_build_task_aggregation_sql,
    _maybe_build_task_listing_sql,
    _maybe_build_user_query_sql,
)


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

    def test_asset_list_query(self):
        sql = _maybe_build_asset_query_sql(
            "list assets",
            {"intent_type": "listing", "entities": ["asset"]},
            56942686,
            self.schema_manifest,
        )
        self.assertIn("COUNT(*) OVER() AS _total_count", sql)
        self.assertIn("FROM asset", sql)
        self.assertIn("asset.company_id = 56942686", sql)
        self.assertIn("ORDER BY asset.id DESC LIMIT 500", sql)

    def test_asset_count_query(self):
        sql = _maybe_build_asset_query_sql(
            "how many assets are there",
            {"intent_type": "aggregation", "entities": ["asset"]},
            56942686,
            self.schema_manifest,
        )
        self.assertIn("COUNT(*) AS total_assets", sql)
        self.assertIn("FROM asset", sql)
        self.assertIn("asset.company_id = 56942686", sql)

    def test_non_asset_query_skips_asset_fast_path(self):
        sql = _maybe_build_asset_query_sql(
            "list users",
            {"intent_type": "listing", "entities": ["user"]},
            56942686,
            self.schema_manifest,
        )
        self.assertEqual(sql, "")

    def test_merge_intent_with_understanding(self):
        merged = _merge_intent_with_understanding(
            {"intent_type": "unknown", "entities": []},
            {"intent": "listing", "entities": ["asset"]},
        )
        self.assertEqual(merged["intent_type"], "listing")
        self.assertEqual(merged["entities"], ["asset"])

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

    def test_task_aggregation_admin_with_person_and_last_30_days(self):
        sql = _maybe_build_task_aggregation_sql(
            "how many tasks for nirmala for last 30 days",
            {
                "intent_type": "aggregation",
                "entities": ["task_transaction"],
                "filter_dict": {"person": "Nirmala", "scheduled_date": "last 30 days"},
            },
            56942686,
            1001,
            "admin",
            self.schema_manifest,
        )
        self.assertIn("COUNT(*) AS total_tasks", sql)
        self.assertIn("LEFT JOIN user u ON tt.assigned_user_id = u.id", sql)
        self.assertIn("LOWER(u.first_name) LIKE '%nirmala%'", sql)
        self.assertIn("DATE(tt.scheduled_date) >= DATE_SUB(CURDATE(), INTERVAL 30 DAY)", sql)
        self.assertIn("f.company_id = 56942686", sql)

    def test_task_aggregation_user_with_last_30_days(self):
        sql = _maybe_build_task_aggregation_sql(
            "how many tasks for last 30 days",
            {
                "intent_type": "aggregation",
                "entities": ["task_transaction"],
                "filter_dict": {"scheduled_date": "last 30 days"},
            },
            56942686,
            2002,
            "user",
            self.schema_manifest,
        )
        self.assertIn("COUNT(*) AS total_tasks", sql)
        self.assertIn("tt.assigned_user_id = 2002", sql)
        self.assertIn("DATE(tt.scheduled_date) >= DATE_SUB(CURDATE(), INTERVAL 30 DAY)", sql)

    def test_task_aggregation_with_last_month(self):
        sql = _maybe_build_task_aggregation_sql(
            "how many tasks for nirmala last month",
            {
                "intent_type": "aggregation",
                "entities": ["task_transaction"],
                "filter_dict": {"person": "Nirmala", "scheduled_date": "last month"},
            },
            56942686,
            1001,
            "admin",
            self.schema_manifest,
        )
        self.assertIn("DATE(tt.scheduled_date) >= DATE_SUB(CURDATE(), INTERVAL 1 MONTH)", sql)
        self.assertIn("DATE(tt.scheduled_date) <= CURDATE()", sql)

    def test_task_aggregation_with_next_week(self):
        sql = _maybe_build_task_aggregation_sql(
            "how many tasks next week",
            {
                "intent_type": "aggregation",
                "entities": ["task_transaction"],
                "filter_dict": {"scheduled_date": "next week"},
            },
            56942686,
            2002,
            "user",
            self.schema_manifest,
        )
        self.assertIn("DATE(tt.scheduled_date) >= CURDATE()", sql)
        self.assertIn("DATE(tt.scheduled_date) <= DATE_ADD(CURDATE(), INTERVAL 7 DAY)", sql)

    def test_task_aggregation_with_explicit_from_to_range(self):
        sql = _maybe_build_task_aggregation_sql(
            "how many tasks for Nirmala from 2026-01-01 to 2026-01-31",
            {
                "intent_type": "aggregation",
                "entities": ["task_transaction"],
                "filter_dict": {"person": "Nirmala"},
            },
            56942686,
            1001,
            "admin",
            self.schema_manifest,
        )
        self.assertIn("DATE(tt.scheduled_date) >= '2026-01-01'", sql)
        self.assertIn("DATE(tt.scheduled_date) <= '2026-01-31'", sql)

    def test_task_listing_pending_tasks_user(self):
        sql = _maybe_build_task_listing_sql(
            "pending tasks",
            {
                "intent_type": "listing",
                "entities": ["task_transaction"],
                "filter_dict": {"status": "Pending"},
            },
            56942686,
            2002,
            "user",
            self.schema_manifest,
        )
        self.assertIn("COUNT(*) OVER() AS _total_count", sql)
        self.assertIn("FROM task_transaction tt", sql)
        self.assertIn("LOWER(tt.status) = 'pending'", sql)
        self.assertIn("tt.assigned_user_id = 2002", sql)
        self.assertIn("ORDER BY tt.id DESC LIMIT 100", sql)


if __name__ == "__main__":
    unittest.main()
