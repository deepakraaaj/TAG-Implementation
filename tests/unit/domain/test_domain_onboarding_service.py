from pathlib import Path

from tools.domain_onboarding import DomainOnboardingService


def _snapshot_with_noise() -> dict:
    return {
        "database_target": "mysql://db.example.com:3306/ops",
        "table_count": 6,
        "tables": [
            {
                "name": "person",
                "columns": [
                    {"name": "id", "type": "INTEGER", "nullable": False},
                    {"name": "first_name", "type": "VARCHAR", "nullable": False},
                    {"name": "last_name", "type": "VARCHAR", "nullable": True},
                    {"name": "company_id", "type": "INTEGER", "nullable": False},
                ],
                "primary_key": ["id"],
                "foreign_keys": [],
                "indexes": [],
            },
            {
                "name": "facility",
                "columns": [
                    {"name": "id", "type": "INTEGER", "nullable": False},
                    {"name": "name", "type": "VARCHAR", "nullable": False},
                    {"name": "company_id", "type": "INTEGER", "nullable": False},
                ],
                "primary_key": ["id"],
                "foreign_keys": [],
                "indexes": [],
            },
            {
                "name": "task_transaction",
                "columns": [
                    {"name": "id", "type": "INTEGER", "nullable": False},
                    {"name": "title", "type": "VARCHAR", "nullable": False},
                    {"name": "status", "type": "VARCHAR", "nullable": False},
                    {"name": "priority", "type": "INTEGER", "nullable": True},
                    {"name": "scheduled_date", "type": "DATETIME", "nullable": True},
                    {"name": "assignee_id", "type": "INTEGER", "nullable": True},
                    {"name": "facility_id", "type": "INTEGER", "nullable": True},
                    {"name": "company_id", "type": "INTEGER", "nullable": False},
                ],
                "primary_key": ["id"],
                "foreign_keys": [
                    {
                        "constrained_columns": ["assignee_id"],
                        "referred_table": "person",
                        "referred_columns": ["id"],
                    },
                    {
                        "constrained_columns": ["facility_id"],
                        "referred_table": "facility",
                        "referred_columns": ["id"],
                    },
                ],
                "indexes": [],
            },
            {
                "name": "facility_asset_mapping",
                "columns": [
                    {"name": "id", "type": "INTEGER", "nullable": False},
                    {"name": "facility_id", "type": "INTEGER", "nullable": False},
                    {"name": "asset_id", "type": "INTEGER", "nullable": False},
                    {"name": "company_id", "type": "INTEGER", "nullable": False},
                ],
                "primary_key": ["id"],
                "foreign_keys": [
                    {
                        "constrained_columns": ["facility_id"],
                        "referred_table": "facility",
                        "referred_columns": ["id"],
                    },
                    {
                        "constrained_columns": ["asset_id"],
                        "referred_table": "asset",
                        "referred_columns": ["id"],
                    },
                ],
                "indexes": [],
            },
            {
                "name": "audit_log",
                "columns": [
                    {"name": "id", "type": "INTEGER", "nullable": False},
                    {"name": "message", "type": "VARCHAR", "nullable": True},
                ],
                "primary_key": ["id"],
                "foreign_keys": [],
                "indexes": [],
            },
            {
                "name": "schema_migrations",
                "columns": [
                    {"name": "version", "type": "VARCHAR", "nullable": False},
                ],
                "primary_key": ["version"],
                "foreign_keys": [],
                "indexes": [],
            },
        ],
    }


def _snapshot_with_override_candidates() -> dict:
    return {
        "database_target": "mysql://db.example.com:3306/incidents",
        "table_count": 5,
        "tables": [
            {
                "name": "staff",
                "columns": [
                    {"name": "id", "type": "INTEGER", "nullable": False},
                    {"name": "first_name", "type": "VARCHAR", "nullable": False},
                    {"name": "last_name", "type": "VARCHAR", "nullable": True},
                    {"name": "company_id", "type": "INTEGER", "nullable": False},
                ],
                "primary_key": ["id"],
                "foreign_keys": [],
                "indexes": [],
            },
            {
                "name": "site",
                "columns": [
                    {"name": "id", "type": "INTEGER", "nullable": False},
                    {"name": "name", "type": "VARCHAR", "nullable": False},
                    {"name": "company_id", "type": "INTEGER", "nullable": False},
                ],
                "primary_key": ["id"],
                "foreign_keys": [],
                "indexes": [],
            },
            {
                "name": "task_transaction",
                "columns": [
                    {"name": "id", "type": "INTEGER", "nullable": False},
                    {"name": "title", "type": "VARCHAR", "nullable": False},
                    {"name": "status", "type": "VARCHAR", "nullable": False},
                    {"name": "scheduled_date", "type": "DATETIME", "nullable": True},
                    {"name": "company_id", "type": "INTEGER", "nullable": False},
                ],
                "primary_key": ["id"],
                "foreign_keys": [],
                "indexes": [],
            },
            {
                "name": "incident",
                "columns": [
                    {"name": "id", "type": "INTEGER", "nullable": False},
                    {"name": "name", "type": "VARCHAR", "nullable": False},
                    {"name": "status", "type": "VARCHAR", "nullable": False},
                    {"name": "occurred_at", "type": "DATETIME", "nullable": True},
                    {"name": "staff_id", "type": "INTEGER", "nullable": True},
                    {"name": "site_id", "type": "INTEGER", "nullable": True},
                    {"name": "company_id", "type": "INTEGER", "nullable": False},
                ],
                "primary_key": ["id"],
                "foreign_keys": [
                    {
                        "constrained_columns": ["staff_id"],
                        "referred_table": "staff",
                        "referred_columns": ["id"],
                    },
                    {
                        "constrained_columns": ["site_id"],
                        "referred_table": "site",
                        "referred_columns": ["id"],
                    },
                ],
                "indexes": [],
            },
            {
                "name": "audit_log",
                "columns": [
                    {"name": "id", "type": "INTEGER", "nullable": False},
                    {"name": "message", "type": "VARCHAR", "nullable": True},
                ],
                "primary_key": ["id"],
                "foreign_keys": [],
                "indexes": [],
            },
        ],
    }


def test_onboarding_service_filters_noise_tables_and_emits_questions():
    service = DomainOnboardingService()

    analysis = service.analyze_snapshot(
        domain_name="ops_auto",
        schema_snapshot=_snapshot_with_noise(),
        description="Generated ops domain",
    )

    assessments = {item.table_name: item for item in analysis.table_assessments}

    assert assessments["audit_log"].category == "noise"
    assert assessments["audit_log"].include is False
    assert assessments["schema_migrations"].category == "noise"
    assert "audit_log" in analysis.excluded_tables
    assert "schema_migrations" in analysis.excluded_tables
    assert "task_transaction" in analysis.included_tables
    assert analysis.artifacts is not None
    assert analysis.artifacts.config_payload()["entity_behavior"]["primary_table"] == "task_transaction"
    assert analysis.artifacts.review_report["table_count"] == len(analysis.included_tables)
    assert any(question.id == "ignore_noise_tables" for question in analysis.clarification_questions)


def test_onboarding_service_redacts_db_target_and_respects_explicit_overrides(monkeypatch):
    service = DomainOnboardingService()
    snapshot = _snapshot_with_override_candidates()
    monkeypatch.setattr(service.generator, "introspect_schema", lambda db_url=None: snapshot)

    analysis = service.analyze(
        domain_name="incident_ops",
        db_url="mysql+mysqlconnector://ops_user:supersecret@db.example.com:3306/incidents",
        primary_table="incident",
        user_table="staff",
        location_table="site",
    )

    config = analysis.artifacts.config_payload() if analysis.artifacts is not None else {}

    assert "supersecret" not in analysis.database_target
    assert analysis.database_target == "mysql+mysqlconnector://db.example.com:3306/incidents"
    assert analysis.connection_source == "provided_db_url"
    assert config["entity_behavior"]["primary_table"] == "incident"
    assert config["user_lookup"]["table"] == "staff"
    assert config["location_lookup"]["table"] == "site"
    assert not any(question.key == "entity_behavior.primary_table" for question in analysis.clarification_questions)


def test_onboarding_service_can_write_analysis_report(tmp_path: Path):
    service = DomainOnboardingService()
    analysis = service.analyze_snapshot(
        domain_name="ops_auto",
        schema_snapshot=_snapshot_with_noise(),
    )

    report_path = service.write_analysis_report(analysis, tmp_path / "onboarding_report.json")

    assert report_path.exists()
    content = report_path.read_text(encoding="utf-8")
    assert "password_redacted" in content
    assert "audit_log" in content
