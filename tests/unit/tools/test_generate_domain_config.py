import json
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace

import scripts.generate_domain as generate_domain


@dataclass
class _FakeArtifacts:
    domain_name: str
    review_report: dict = field(default_factory=lambda: {"needs_review": []})
    root_json_files: dict = field(default_factory=dict)
    written_files: list[str] = field(default_factory=list)

    def manifest_payload(self) -> dict:
        return {"tables": {}}


class _FakeDomainGenerationService:
    last_instance = None

    def __init__(self) -> None:
        type(self).last_instance = self
        self.introspected_db_url = None
        self.last_write_force = None
        self.last_description = None
        self.last_metadata_hints = None

    def merge_metadata_hints(self, base, extra):
        merged = dict(base or {})
        merged.update(dict(extra or {}))
        return merged

    def introspect_schema(self, db_url=None):
        self.introspected_db_url = db_url
        return {
            "database_target": "sqlite:///example.db",
            "table_count": 0,
            "tables": [],
        }

    def build_artifacts(self, domain_name, snapshot, description="", metadata_hints=None):
        self.last_description = description
        self.last_metadata_hints = dict(metadata_hints or {})
        return _FakeArtifacts(domain_name=str(domain_name or "").strip() or "ops_auto")

    def build_semantics_template(self, schema_snapshot, artifacts, metadata_hints=None):
        return {
            "completed": False,
            "domain": artifacts.domain_name,
        }

    def write_artifacts(self, artifacts, output_root, force=False):
        self.last_write_force = force
        artifacts.written_files = ["generated/domain.json"]
        return artifacts


class _FakeDomainOnboardingService:
    last_instance = None

    def __init__(self, generator=None) -> None:
        type(self).last_instance = self
        self.generator = generator
        self.last_analysis = None

    def analyze_snapshot(
        self,
        *,
        domain_name,
        schema_snapshot,
        description="",
        metadata_hints=None,
        include_tables=None,
        exclude_tables=None,
        connection_source="snapshot",
        database_target="",
    ):
        included_tables = list(include_tables or ["trip", "user", "location"])
        excluded_tables = list(exclude_tables or ["audit_log"])
        analysis = SimpleNamespace(
            domain_name=domain_name,
            database_target=database_target or "sqlite:///example.db",
            connection_source=connection_source,
            included_tables=included_tables,
            excluded_tables=excluded_tables,
            clarification_questions=[],
            artifacts=_FakeArtifacts(domain_name=str(domain_name or "").strip() or "ops_auto"),
        )
        self.last_analysis = analysis
        return analysis

    def write_analysis_report(self, analysis, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{}\n", encoding="utf-8")
        return path


class _FailingDomainGenerationService(_FakeDomainGenerationService):
    def introspect_schema(self, db_url=None):
        raise RuntimeError("db access denied")


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def test_generate_config_writes_request_template(tmp_path: Path):
    config_path = tmp_path / "maintenance_request.json"

    exit_code = generate_domain.main(
        [
            "--generate-config",
            "--config-file",
            str(config_path),
            "--domain",
            "maintenance_ops",
            "--guided",
        ]
    )

    payload = _read_json(config_path)

    assert exit_code == 0
    assert "_chatgpt_prompt" in payload
    assert payload["status"]["generated"] is False
    assert payload["status"]["state"] == "draft"
    assert payload["request"]["domain"] == "maintenance_ops"
    assert payload["request"]["guided"] is True
    assert "metadata_hints" in payload["request"]
    assert "clarification_hints" in payload["request"]
    assert payload["request"]["output_root"] == "domains"


def test_config_file_can_drive_template_generation_and_updates_status(tmp_path: Path, monkeypatch):
    config_path = tmp_path / "generation_request.json"
    output_root = tmp_path / "domains"
    config_path.write_text(
        json.dumps(
            {
                "request": {
                    "domain": "ops_auto",
                    "db_url": "mysql://from-config",
                    "output_root": output_root.as_posix(),
                    "generate_template": True,
                }
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(generate_domain, "DomainGenerationService", _FakeDomainGenerationService)
    monkeypatch.setattr(generate_domain, "DomainOnboardingService", _FakeDomainOnboardingService)

    exit_code = generate_domain.main(["--config-file", str(config_path)])
    payload = _read_json(config_path)
    template_path = output_root / "ops_auto" / "semantics_template.json"

    assert exit_code == 0
    assert payload["status"]["generated"] is False
    assert payload["status"]["template_generated"] is True
    assert payload["status"]["state"] == "template_generated"
    assert payload["result"]["semantics_template"] == template_path.as_posix()
    assert template_path.exists()
    assert _FakeDomainGenerationService.last_instance.introspected_db_url == "mysql://from-config"


def test_cli_values_override_config_file_values(tmp_path: Path, monkeypatch):
    config_path = tmp_path / "generation_request.json"
    output_root = tmp_path / "domains"
    config_path.write_text(
        json.dumps(
            {
                "request": {
                    "domain": "ops_auto",
                    "db_url": "mysql://from-config",
                    "output_root": output_root.as_posix(),
                    "force": False,
                }
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(generate_domain, "DomainGenerationService", _FakeDomainGenerationService)
    monkeypatch.setattr(generate_domain, "DomainOnboardingService", _FakeDomainOnboardingService)

    exit_code = generate_domain.main(
        [
            "--config-file",
            str(config_path),
            "--db-url",
            "mysql://from-cli",
            "--force",
        ]
    )
    payload = _read_json(config_path)

    assert exit_code == 0
    assert payload["status"]["generated"] is True
    assert payload["status"]["template_generated"] is False
    assert payload["status"]["state"] == "completed"
    assert payload["result"]["written_files"] == 1
    assert _FakeDomainGenerationService.last_instance.introspected_db_url == "mysql://from-cli"
    assert _FakeDomainGenerationService.last_instance.last_write_force is True


def test_default_repo_config_file_is_auto_loaded(tmp_path: Path, monkeypatch):
    config_path = tmp_path / "generate_domain.request.json"
    output_root = tmp_path / "domains"
    config_path.write_text(
        json.dumps(
            {
                "request": {
                    "domain": "ops_auto",
                    "db_url": "mysql://from-default-config",
                    "output_root": output_root.as_posix(),
                }
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(generate_domain, "DEFAULT_RUN_CONFIG_PATH", config_path)
    monkeypatch.setattr(generate_domain, "DomainGenerationService", _FakeDomainGenerationService)
    monkeypatch.setattr(generate_domain, "DomainOnboardingService", _FakeDomainOnboardingService)

    exit_code = generate_domain.main([])
    payload = _read_json(config_path)

    assert exit_code == 0
    assert payload["status"]["generated"] is True
    assert payload["status"]["state"] == "completed"
    assert _FakeDomainGenerationService.last_instance.introspected_db_url == "mysql://from-default-config"


def test_inline_metadata_and_app_name_can_live_in_single_json_file(tmp_path: Path, monkeypatch):
    config_path = tmp_path / "generation_request.json"
    output_root = tmp_path / "domains"
    config_path.write_text(
        json.dumps(
            {
                "request": {
                    "domain": "ops_auto",
                    "app_name": "Field Ops Assistant",
                    "db_url": "mysql://from-config",
                    "output_root": output_root.as_posix(),
                    "metadata_hints": {
                        "scope": "field operations for incidents and dispatch",
                        "example_queries": ["show open incidents"],
                    },
                    "clarification_hints": {
                        "enum_values": {"status": "0=Open, 1=Closed"},
                    },
                }
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(generate_domain, "DomainGenerationService", _FakeDomainGenerationService)
    monkeypatch.setattr(generate_domain, "DomainOnboardingService", _FakeDomainOnboardingService)

    exit_code = generate_domain.main(["--config-file", str(config_path)])
    payload = _read_json(config_path)
    fake_service = _FakeDomainGenerationService.last_instance

    assert exit_code == 0
    assert payload["status"]["generated"] is True
    assert payload["status"]["state"] == "completed"
    assert fake_service.last_description == "Field Ops Assistant"
    assert fake_service.last_metadata_hints["scope"] == "field operations for incidents and dispatch"
    assert fake_service.last_metadata_hints["example_queries"] == ["show open incidents"]


def test_early_connection_failure_updates_status_to_failed(tmp_path: Path, monkeypatch):
    config_path = tmp_path / "generation_request.json"
    config_path.write_text(
        json.dumps(
            {
                "request": {
                    "domain": "ops_auto",
                    "db_url": "mysql://broken",
                    "output_root": (tmp_path / "domains").as_posix(),
                }
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(generate_domain, "DomainGenerationService", _FailingDomainGenerationService)
    monkeypatch.setattr(generate_domain, "DomainOnboardingService", _FakeDomainOnboardingService)

    try:
        generate_domain.main(["--config-file", str(config_path)])
    except RuntimeError as exc:
        assert str(exc) == "db access denied"
    else:
        raise AssertionError("expected the generator to fail")

    payload = _read_json(config_path)
    assert payload["status"]["generated"] is False
    assert payload["status"]["state"] == "failed"
    assert payload["result"]["error"] == "db access denied"


def test_guided_config_run_is_non_interactive_by_default(tmp_path: Path, monkeypatch):
    config_path = tmp_path / "generation_request.json"
    output_root = tmp_path / "domains"
    config_path.write_text(
        json.dumps(
            {
                "request": {
                    "domain": "ops_auto",
                    "db_url": "mysql://from-config",
                    "output_root": output_root.as_posix(),
                    "guided": True,
                    "developer_clarifications": True
                }
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(generate_domain, "DomainGenerationService", _FakeDomainGenerationService)
    monkeypatch.setattr(generate_domain, "DomainOnboardingService", _FakeDomainOnboardingService)
    monkeypatch.setattr(generate_domain, "_ask_list", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("should not prompt for list input")))
    monkeypatch.setattr(generate_domain, "_collect_answers", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("should not prompt for clarification answers")))

    exit_code = generate_domain.main(["--config-file", str(config_path)])
    payload = _read_json(config_path)

    assert exit_code == 0
    assert payload["status"]["generated"] is True
    assert payload["status"]["state"] == "completed"
    assert payload["result"]["onboarding_report"].endswith("/ops_auto/onboarding_report.json")
