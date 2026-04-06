from app.services.data.sql_validator import SQLValidatorService


def test_sql_validator_allows_unfiltered_select_when_app_override_disables_requirement():
    validator = SQLValidatorService(allow_mutations=False, require_select_where=True)

    assert validator.validate_sql(
        "SELECT id FROM trip;",
        allowed_tables_override=["trip"],
        require_select_where_override=False,
    ) is True


def test_sql_validator_blocks_protected_table_override():
    validator = SQLValidatorService(allow_mutations=False)

    assert validator.validate_sql(
        "SELECT id FROM flyway_schema_history WHERE installed_rank = 1;",
        allowed_tables_override=["flyway_schema_history"],
        protected_tables_override=["flyway_schema_history"],
    ) is False


def test_sql_validator_allows_count_wrapper_when_inner_select_has_where():
    validator = SQLValidatorService(allow_mutations=False, require_select_where=True)

    assert validator.validate_sql(
        (
            "SELECT COUNT(*) AS total_count "
            "FROM (SELECT id, vehicle_number FROM vehicle WHERE company_id = 1) count_rows;"
        ),
        allowed_tables_override=["vehicle"],
    ) is True


def test_sql_validator_rejects_count_wrapper_when_inner_select_has_no_where():
    validator = SQLValidatorService(allow_mutations=False, require_select_where=True)

    assert validator.validate_sql(
        "SELECT COUNT(*) AS total_count FROM (SELECT id FROM vehicle) count_rows;",
        allowed_tables_override=["vehicle"],
    ) is False
