from app.services.data.sql_validator import SQLValidatorService


def test_sql_validator_rejects_update_without_where():
    validator = SQLValidatorService()
    assert validator.validate_sql("UPDATE task_transaction SET status = 2;") is False


def test_sql_validator_accepts_update_with_where_when_mutations_allowed():
    validator = SQLValidatorService()
    assert validator.validate_sql("UPDATE task_transaction SET status = 2 WHERE id = 10;") is True


def test_sql_validator_rejects_mutation_when_policy_disables_it():
    validator = SQLValidatorService(allow_mutations=True)
    assert (
        validator.validate_sql(
            "INSERT INTO asset (name, company_id) VALUES ('x', 1);",
            allow_mutations_override=False,
        )
        is False
    )


def test_sql_validator_rejects_system_schema_tables():
    validator = SQLValidatorService()
    assert validator.validate_sql("SELECT table_name FROM information_schema.tables WHERE table_schema='tag';") is False


def test_sql_validator_allowlist_is_case_insensitive():
    validator = SQLValidatorService(allowed_tables=["task_transaction"])
    assert validator.validate_sql("SELECT id FROM TASK_TRANSACTION WHERE id = 1;") is True
