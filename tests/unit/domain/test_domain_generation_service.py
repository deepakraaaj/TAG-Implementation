from pathlib import Path

from app.domains.generator import DomainGenerationService
from app.domains.registry import DomainRegistry


def _snapshot() -> dict:
    return {
        "database_target": "sqlite:///example.db",
        "table_count": 3,
        "tables": [
            {
                "name": "person",
                "columns": [
                    {"name": "id", "type": "INTEGER", "nullable": False},
                    {"name": "first_name", "type": "VARCHAR", "nullable": False},
                    {"name": "last_name", "type": "VARCHAR", "nullable": True},
                    {"name": "email", "type": "VARCHAR", "nullable": True},
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
        ],
    }


def _metadata_hints() -> dict:
    return {
        "scope": "warehouse operations including work orders, technicians, and zones",
        "business_terms": {
            "backlog": "Open work orders not yet started",
        },
        "entities": {
            "task_transaction": {
                "label": "work orders",
                "aliases": ["ticket", "tickets", "job"],
                "description": "Operational work orders raised by the warehouse team",
                "example_queries": ["show open work orders", "count overdue tickets"],
            },
            "person": {
                "label": "technicians",
                "aliases": ["technician", "tech", "techs"],
                "example_queries": ["list technicians"],
            },
            "facility": {
                "label": "zones",
                "aliases": ["zone", "zones"],
                "example_queries": ["show work orders in zone A"],
            },
        },
        "categorized_examples": {
            "Work Orders": ["show open work orders"],
            "Actions": ["create work order"],
        },
        "workflows": [
            {
                "workflow_id": "create_work_order",
                "table": "task_transaction",
                "operation": "insert",
                "label": "Create work order",
                "trigger_phrases": ["create work order", "log ticket"],
                "required_fields": ["title", "assignee_id", "facility_id"],
                "reasoning": "Core warehouse intake action",
                "confidence": 95,
            }
        ],
    }


def test_build_artifacts_emits_valid_domain_payload():
    service = DomainGenerationService()

    artifacts = service.build_artifacts("ops_auto", _snapshot(), description="Generated ops domain")

    config = artifacts.config_payload()
    manifest = artifacts.manifest_payload()

    assert config["name"] == "ops_auto"
    assert config["entity_behavior"]["primary_table"] == "task_transaction"
    assert config["user_lookup"]["table"] == "person"
    assert config["location_lookup"]["table"] == "facility"
    assert config["domain_knowledge"]["reasoning_profile"]["name"] == "ClearTM canonical AI reasoning"
    assert config["domain_knowledge"]["scope"].startswith("ops auto operations including")
    assert "task_transaction" in manifest["tables"]
    assert "task_transaction" in manifest["query_templates"]
    assert artifacts.review_report["inference_summary"]["primary_table"]["value"] == "task_transaction"

    DomainRegistry.validate_domain_artifacts(config, manifest, domain_name="ops_auto")


def test_build_artifacts_merges_metadata_hints_into_domain_knowledge():
    service = DomainGenerationService()

    artifacts = service.build_artifacts("warehouse_ops", _snapshot(), metadata_hints=_metadata_hints())

    config = artifacts.config_payload()
    manifest = artifacts.manifest_payload()

    assert config["domain_knowledge"]["scope"] == "warehouse operations including work orders, technicians, and zones"
    assert config["domain_knowledge"]["business_terms"]["backlog"] == "Open work orders not yet started"
    assert config["domain_knowledge"]["workflows"][0]["workflow_id"] == "create_work_order"
    assert config["capabilities"]["categorized_examples"]["Actions"] == ["create work order"]
    assert "tickets" in config["entity_behavior"]["primary_keywords"]
    assert "tickets" in manifest["tables"]["task_transaction"]["aliases"]
    assert artifacts.review_report["metadata_hints_applied"]["workflow_count"] == 1


def test_write_artifacts_creates_generated_structure(tmp_path: Path):
    service = DomainGenerationService()
    artifacts = service.build_artifacts("ops_auto", _snapshot())

    written = service.write_artifacts(artifacts, output_root=tmp_path)
    domain_dir = tmp_path / "ops_auto"

    assert (domain_dir / "generated" / "domain.json").exists()
    assert (domain_dir / "generated" / "domain_knowledge.json").exists()
    assert (domain_dir / "generated" / "entity_behavior.json").exists()
    assert (domain_dir / "generated" / "manifest" / "tables.json").exists()
    assert (domain_dir / "reports.json").exists()
    assert (domain_dir / "review_report.json").exists()
    assert (domain_dir / "manual" / "README.md").exists()
    assert (domain_dir / "enums.py").exists()
    assert written.written_files


def test_build_artifacts_marks_uncertain_inference_for_sparse_schema():
    service = DomainGenerationService()
    sparse_snapshot = {
        "database_target": "sqlite:///example.db",
        "table_count": 1,
        "tables": [
            {
                "name": "record_log",
                "columns": [
                    {"name": "id", "type": "INTEGER", "nullable": False},
                    {"name": "message", "type": "VARCHAR", "nullable": True},
                ],
                "primary_key": ["id"],
                "foreign_keys": [],
                "indexes": [],
            }
        ],
    }

    artifacts = service.build_artifacts("sparse_ops", sparse_snapshot)

    needs_review = artifacts.review_report.get("needs_review") or []
    assert needs_review
    assert any(item["key"] == "entity_behavior.primary_table" for item in needs_review)


def test_build_artifacts_infers_workflow_candidates_without_metadata_hints():
    service = DomainGenerationService()

    artifacts = service.build_artifacts("ops_auto", _snapshot())

    workflows = artifacts.config_payload()["domain_knowledge"]["workflows"]
    needs_review = artifacts.review_report.get("needs_review") or []

    assert workflows
    assert any(item["workflow_id"].startswith("create_") for item in workflows)
    assert any(item["key"] == "domain_knowledge.workflows" for item in needs_review)
