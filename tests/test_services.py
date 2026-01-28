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
