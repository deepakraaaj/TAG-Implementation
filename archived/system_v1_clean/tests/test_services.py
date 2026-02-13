import unittest
from app.services.sql_validator import SQLValidatorService
from app.services.pii_service import PIIService


class TestServices(unittest.TestCase):
    def test_sql_validator(self):
        validator = SQLValidatorService(allowed_tables=["users", "orders"])

        # Test safe query
        self.assertTrue(validator.validate_sql("SELECT * FROM users"))

        # Test forbidden command
        self.assertFalse(validator.validate_sql("DROP TABLE users"))
        self.assertFalse(validator.validate_sql("DELETE FROM users WHERE id=1"))

        # Test forbidden table
        self.assertFalse(validator.validate_sql("SELECT * FROM secrets"))

    def test_sql_validator_rejects_unknown_qualified_column(self):
        validator = SQLValidatorService()
        table_columns = {
            "user": {"id", "first_name", "email_id"},
            "facility_user_mapping": {"id", "user_id", "asset_id", "company_id"},
        }
        sql = (
            "SELECT u.id, u.password_reset_token "
            "FROM facility_user_mapping f JOIN user u ON f.user_id = u.id"
        )
        self.assertFalse(validator.validate_sql(sql, table_columns=table_columns))

    def test_sql_validator_allows_known_qualified_columns(self):
        validator = SQLValidatorService()
        table_columns = {
            "user": {"id", "first_name", "email_id"},
            "facility_user_mapping": {"id", "user_id", "asset_id", "company_id"},
        }
        sql = (
            "SELECT u.id, u.first_name "
            "FROM facility_user_mapping f JOIN user u ON f.user_id = u.id"
        )
        self.assertTrue(validator.validate_sql(sql, table_columns=table_columns))

    def test_sql_validator_rejects_duplicate_table_alias(self):
        validator = SQLValidatorService()
        sql = (
            "SELECT a.id FROM facility_asset_mapping a "
            "JOIN asset a ON a.id = a.id"
        )
        self.assertFalse(validator.validate_sql(sql))

    def test_pii_service(self):
        pii_service = PIIService()
        text = "Contact me at test@example.com or 123-456-7890."
        sanitized = pii_service.sanitize(text)

        self.assertIn("<EMAIL>", sanitized)
        self.assertIn("<PHONE>", sanitized)
        self.assertNotIn("test@example.com", sanitized)
        self.assertNotIn("123-456-7890", sanitized)


if __name__ == '__main__':
    unittest.main()
