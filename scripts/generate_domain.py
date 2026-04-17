#!/usr/bin/env python3
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
from typing import Any, Dict, List

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools.domain_onboarding import ClarificationQuestion, DomainGenerationService, DomainOnboardingService

DEFAULT_OUTPUT_ROOT = "domains"
RUN_CONFIG_FILENAME = "generation_request.json"
RUN_CONFIG_VERSION = 1
DEFAULT_RUN_CONFIG_PATH = REPO_ROOT / "scripts" / "generate_domain.request.json"


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Generate a domain package from a live database schema.",
    )
    parser.add_argument(
        "--config-file",
        default="",
        help="Optional JSON config file for the generator. CLI flags override values loaded from this file.",
    )
    parser.add_argument(
        "--generate-config",
        action="store_true",
        help="Write a reusable JSON config file for the generator and exit.",
    )
    parser.add_argument(
        "--domain",
        default=None,
        help="Domain name to generate, for example: maintenance_v2. Required unless provided in --config-file.",
    )
    parser.add_argument(
        "--app-name",
        default=None,
        help="Optional human-friendly app/assistant name stored in the JSON config and used as a fallback description.",
    )
    parser.add_argument(
        "--db-url",
        default=None,
        help="Database URL to inspect. Defaults to DATABASE_URL, or DATABASE_URL_DOCKER in --simple mode.",
    )
    parser.add_argument(
        "--description",
        default=None,
        help="Optional domain description override.",
    )
    parser.add_argument(
        "--output-root",
        default=None,
        help="Root folder where the domain package should be written.",
    )
    parser.add_argument(
        "--metadata-file",
        default=None,
        help="Optional JSON file with project vocabulary, examples, and workflow hints.",
    )
    parser.add_argument(
        "--include-table",
        action="append",
        default=None,
        help="Force-include a table in generation. Repeatable.",
    )
    parser.add_argument(
        "--exclude-table",
        action="append",
        default=None,
        help="Exclude a table from generation. Repeatable.",
    )
    parser.add_argument(
        "--clarification-file",
        default=None,
        help="Optional JSON file with previously approved developer clarifications.",
    )
    parser.add_argument(
        "--developer-clarifications",
        action="store_true",
        default=None,
        help="Ask the developer targeted clarification questions before writing the domain package.",
    )
    parser.add_argument(
        "--interactive-prompts",
        action="store_true",
        default=None,
        help="Allow interactive onboarding/clarification prompts during generation.",
    )
    parser.add_argument(
        "--simple",
        action="store_true",
        default=None,
        help="Simple mode: prefer DATABASE_URL_DOCKER when available and ask only the minimal table-meaning questions.",
    )
    parser.add_argument(
        "--guided",
        action="store_true",
        default=None,
        help="Run schema triage plus a short guided interview before writing the domain package.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        default=None,
        help="Overwrite known generated files if the domain folder already exists.",
    )
    parser.add_argument(
        "--generate-template",
        action="store_true",
        default=None,
        help=(
            "Write a semantics_template.json file with _TODO placeholders for enum values, "
            "column descriptions, and business terms. Fill it in your IDE, set completed=true, "
            "then pass it via --clarification-file on the next run."
        ),
    )
    return parser


def parse_args(argv: List[str] | None = None) -> argparse.Namespace:
    return _build_parser().parse_args(argv)


def _load_optional_json(path: Path | None) -> Dict[str, Any]:
    if path is None or not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    return dict(payload) if isinstance(payload, dict) else {}


def _write_json(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=False)
        handle.write("\n")


def _normalized_domain_name(raw: str) -> str:
    return str(raw or "").strip().lower().replace(" ", "_")


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _config_path_from_args(args: argparse.Namespace) -> Path | None:
    raw_config_path = str(getattr(args, "config_file", "") or "").strip()
    if raw_config_path:
        return Path(raw_config_path)
    if not bool(getattr(args, "generate_config", False)):
        if DEFAULT_RUN_CONFIG_PATH.exists():
            return DEFAULT_RUN_CONFIG_PATH
        return None
    domain_name = _normalized_domain_name(str(getattr(args, "domain", "") or "").strip())
    if not domain_name:
        return DEFAULT_RUN_CONFIG_PATH
    output_root = str(getattr(args, "output_root", "") or "").strip() or DEFAULT_OUTPUT_ROOT
    return Path(output_root) / domain_name / RUN_CONFIG_FILENAME


def _string_value(value: Any) -> str:
    return str(value or "").strip()


def _bool_value(value: Any, *, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off"}:
            return False
    return bool(value)


def _list_value(values: Any) -> List[str]:
    if isinstance(values, str):
        return _parse_csv_values(values)
    return _clean_list(values)


def _build_run_config_payload(args: argparse.Namespace, *, config_path: Path) -> Dict[str, Any]:
    command_example = f"python scripts/generate_domain.py --config-file {config_path.as_posix()}"
    return {
        "_instructions": [
            "Fill request values, then rerun the generator using the config file.",
            "CLI flags override values loaded from request.",
            "The status block is maintained by the script after each run.",
        ],
        "_chatgpt_prompt": [
            "Act as a TAG domain onboarding assistant.",
            "Your job is to help the user complete this JSON for scripts/generate_domain.py.",
            "Ask short questions one at a time and wait for the user's answer before asking the next question.",
            "Collect at least: request.domain, request.app_name, request.db_url, request.description or request.metadata_hints.scope, and 2-4 request.metadata_hints.example_queries.",
            "Also ask for business terms, likely primary/user/location tables, include/exclude tables, entity labels/descriptions, and any enum meanings if the user knows them.",
            "Do not invent database URLs, table names, or business facts. Leave unknown fields empty.",
            "Set request.interactive_prompts to false unless the user explicitly wants terminal prompts.",
            "When enough information is collected, reply with the completed JSON only in a json code block.",
            "Keep all existing keys in the JSON and preserve valid JSON syntax."
        ],
        "version": RUN_CONFIG_VERSION,
        "status": {
            "generated": False,
            "template_generated": False,
            "state": "draft",
            "message": f"Fill request values, then run: {command_example}",
            "updated_at": _now_iso(),
            "last_run_started_at": None,
            "last_run_finished_at": None,
        },
        "request": {
            "domain": _string_value(getattr(args, "domain", None)) or "your_domain",
            "app_name": _string_value(getattr(args, "app_name", None)),
            "db_url": _string_value(getattr(args, "db_url", None)),
            "description": _string_value(getattr(args, "description", None)),
            "output_root": _string_value(getattr(args, "output_root", None)) or DEFAULT_OUTPUT_ROOT,
            "metadata_file": _string_value(getattr(args, "metadata_file", None)),
            "clarification_file": _string_value(getattr(args, "clarification_file", None)),
            "include_tables": _list_value(getattr(args, "include_table", None)),
            "exclude_tables": _list_value(getattr(args, "exclude_table", None)),
            "developer_clarifications": _bool_value(getattr(args, "developer_clarifications", None)),
            "interactive_prompts": False,
            "simple": _bool_value(getattr(args, "simple", None)),
            "guided": _bool_value(getattr(args, "guided", None)),
            "force": _bool_value(getattr(args, "force", None)),
            "generate_template": _bool_value(getattr(args, "generate_template", None)),
            "metadata_hints": {
                "scope": "",
                "example_queries": [],
                "business_terms": {},
                "table_roles": {
                    "primary_table": "",
                    "user_table": "",
                    "location_table": "",
                },
                "entities": {},
                "categorized_examples": {},
                "workflows": [],
            },
            "clarification_hints": {
                "enum_values": {},
                "column_descriptions": {},
                "extra_business_terms": "",
            },
        },
        "result": {},
    }


def _load_run_config(path: Path) -> Dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"Config file not found: {path.as_posix()}")
    payload = _load_optional_json(path)
    request = payload.get("request")
    if request is None:
        payload["request"] = {}
    elif not isinstance(request, dict):
        raise ValueError("Generator config file must contain an object at request.")
    status = payload.get("status")
    if status is None:
        payload["status"] = {}
    elif not isinstance(status, dict):
        raise ValueError("Generator config file must contain an object at status.")
    result = payload.get("result")
    if result is None:
        payload["result"] = {}
    elif not isinstance(result, dict):
        raise ValueError("Generator config file must contain an object at result.")
    payload["version"] = int(payload.get("version") or RUN_CONFIG_VERSION)
    return payload


def _resolve_string_arg(
    cli_value: Any,
    config_request: Dict[str, Any],
    key: str,
    *,
    default: str = "",
    config_path: Path | None = None,
    resolve_relative_to_config: bool = False,
) -> str:
    raw_value = cli_value if cli_value is not None else config_request.get(key)
    value = _string_value(raw_value)
    if not value:
        return default
    if resolve_relative_to_config and config_path is not None:
        candidate = Path(value)
        if not candidate.is_absolute():
            return (config_path.parent / candidate).as_posix()
    return value


def _resolve_list_arg(
    cli_value: Any,
    config_request: Dict[str, Any],
    config_key: str,
) -> List[str]:
    if cli_value is not None:
        return _list_value(cli_value)
    return _list_value(config_request.get(config_key))


def _resolve_bool_arg(
    cli_value: Any,
    config_request: Dict[str, Any],
    key: str,
    *,
    default: bool = False,
) -> bool:
    if cli_value is not None:
        return _bool_value(cli_value)
    if key not in config_request:
        return default
    return _bool_value(config_request.get(key))


def _resolve_interactive_prompts(
    cli_value: Any,
    config_request: Dict[str, Any],
    *,
    has_config: bool,
) -> bool:
    if cli_value is not None:
        return _bool_value(cli_value)
    if "interactive_prompts" in config_request:
        return _bool_value(config_request.get("interactive_prompts"))
    return not has_config


def _apply_run_config(
    args: argparse.Namespace,
    config_payload: Dict[str, Any] | None,
    *,
    config_path: Path | None = None,
) -> argparse.Namespace:
    request = dict((config_payload or {}).get("request") or {})
    has_config = config_payload is not None
    resolved = argparse.Namespace(**vars(args))
    resolved.domain = _resolve_string_arg(args.domain, request, "domain")
    resolved.app_name = _resolve_string_arg(getattr(args, "app_name", None), request, "app_name")
    resolved.db_url = _resolve_string_arg(args.db_url, request, "db_url")
    resolved.description = (
        _resolve_string_arg(args.description, request, "description")
        or resolved.app_name
    )
    resolved.output_root = _resolve_string_arg(args.output_root, request, "output_root", default=DEFAULT_OUTPUT_ROOT)
    resolved.metadata_file = _resolve_string_arg(
        args.metadata_file,
        request,
        "metadata_file",
        config_path=config_path,
        resolve_relative_to_config=True,
    )
    resolved.clarification_file = _resolve_string_arg(
        args.clarification_file,
        request,
        "clarification_file",
        config_path=config_path,
        resolve_relative_to_config=True,
    )
    resolved.include_table = _resolve_list_arg(args.include_table, request, "include_tables")
    resolved.exclude_table = _resolve_list_arg(args.exclude_table, request, "exclude_tables")
    resolved.developer_clarifications = _resolve_bool_arg(args.developer_clarifications, request, "developer_clarifications")
    resolved.simple = _resolve_bool_arg(args.simple, request, "simple")
    resolved.guided = _resolve_bool_arg(args.guided, request, "guided")
    resolved.force = _resolve_bool_arg(args.force, request, "force")
    resolved.generate_template = _resolve_bool_arg(args.generate_template, request, "generate_template")
    resolved.interactive_prompts = _resolve_interactive_prompts(
        getattr(args, "interactive_prompts", None),
        request,
        has_config=has_config,
    )
    return resolved


def _write_run_config_status(
    config_path: Path,
    config_payload: Dict[str, Any],
    *,
    state: str,
    message: str,
    started_at: str | None = None,
    finished_at: str | None = None,
    result: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    updated_payload = dict(config_payload or {})
    updated_payload["version"] = int(updated_payload.get("version") or RUN_CONFIG_VERSION)
    status = dict(updated_payload.get("status") or {})
    status["generated"] = state == "completed"
    status["template_generated"] = state == "template_generated"
    status["state"] = state
    status["message"] = message
    status["updated_at"] = _now_iso()
    if started_at is not None:
        status["last_run_started_at"] = started_at
    else:
        status.setdefault("last_run_started_at", None)
    if finished_at is not None:
        status["last_run_finished_at"] = finished_at
    elif state == "running":
        status["last_run_finished_at"] = None
    else:
        status.setdefault("last_run_finished_at", None)
    updated_payload["status"] = status
    if result is not None:
        updated_payload["result"] = dict(result)
    else:
        updated_payload.setdefault("result", {})
    _write_json(config_path, updated_payload)
    return updated_payload


def _request_object(config_payload: Dict[str, Any] | None, key: str) -> Dict[str, Any]:
    request = (config_payload or {}).get("request")
    if not isinstance(request, dict):
        return {}
    payload = request.get(key)
    return dict(payload) if isinstance(payload, dict) else {}


def _clean_todo_values(data: Any) -> Any:
    """Remove _TODO placeholder values from a loaded template so they don't pollute hints."""
    if isinstance(data, dict):
        cleaned = {}
        for key, value in data.items():
            if str(key).startswith("_"):
                continue  # skip _instructions, _comment
            clean_value = _clean_todo_values(value)
            if clean_value is not None:
                cleaned[key] = clean_value
        return cleaned if cleaned else None
    if isinstance(data, str):
        stripped = data.strip()
        if stripped.startswith("_TODO") or not stripped:
            return None
        return stripped
    if isinstance(data, list):
        return [_clean_todo_values(item) for item in data if _clean_todo_values(item) is not None]
    return data


def _load_template_as_hints(path: Path | None) -> Dict[str, Any]:
    """Load a filled semantics template, removing _TODO values and _instructions."""
    raw = _load_optional_json(path)
    if not raw:
        return {}
    if not raw.get("completed"):
        return {}  # Developer hasn't marked it as completed yet
    cleaned = _clean_todo_values(raw)
    return dict(cleaned) if isinstance(cleaned, dict) else {}


def _default_text(question: ClarificationQuestion) -> str:
    value = question.default_value
    if isinstance(value, list):
        return ", ".join(str(item or "").strip() for item in value if str(item or "").strip())
    return str(value or "").strip()


def _parse_csv_values(raw: str) -> List[str]:
    return [item.strip() for item in str(raw or "").split(",") if item.strip()]


def _clean_list(values: Any) -> List[str]:
    if not isinstance(values, list):
        return []
    return [str(item or "").strip() for item in values if str(item or "").strip()]


def _preview_values(values: List[str], *, limit: int = 10) -> str:
    cleaned = [str(item or "").strip() for item in values if str(item or "").strip()]
    if not cleaned:
        return "none"
    preview = ", ".join(cleaned[:limit])
    if len(cleaned) > limit:
        preview += ", ..."
    return preview


def _ask_list(prompt: str, *, default_values: List[str] | None = None) -> List[str]:
    default_values = [str(item or "").strip() for item in (default_values or []) if str(item or "").strip()]
    suffix = f" [{', '.join(default_values)}]" if default_values else " [optional]"
    raw = input(f"{prompt}{suffix}: ").strip()
    if not raw:
        return list(default_values)
    lowered = raw.lower()
    if lowered in {"none", "clear", "null", "n/a"}:
        return []
    return _parse_csv_values(raw)


def _table_hint_values(metadata_hints: Dict[str, Any], key: str) -> List[str]:
    return _clean_list((metadata_hints or {}).get(key))


def _merge_table_hints(
    service: DomainGenerationService,
    metadata_hints: Dict[str, Any],
    *,
    include_tables: List[str] | None = None,
    exclude_tables: List[str] | None = None,
) -> Dict[str, Any]:
    override: Dict[str, Any] = {}
    if include_tables is not None:
        override["include_tables"] = list(include_tables)
    if exclude_tables is not None:
        override["exclude_tables"] = list(exclude_tables)
    if not override:
        return dict(metadata_hints or {})
    return service.merge_metadata_hints(metadata_hints, override)


def _connection_source(explicit_db_url: str, *, simple_mode: bool) -> str:
    if str(explicit_db_url or "").strip():
        return "provided_db_url"
    if simple_mode and str(os.getenv("DATABASE_URL_DOCKER") or "").strip():
        return "env.DATABASE_URL_DOCKER"
    return "settings.DATABASE_URL"


def _filter_snapshot(schema_snapshot: Dict[str, Any], included_tables: List[str]) -> Dict[str, Any]:
    included_lookup = {str(name or "").strip() for name in included_tables if str(name or "").strip()}
    return {
        "database_target": str(schema_snapshot.get("database_target") or ""),
        "table_count": len(included_lookup),
        "tables": [
            dict(table)
            for table in (schema_snapshot.get("tables") or [])
            if str((table or {}).get("name") or "").strip() in included_lookup
        ],
    }


def _print_onboarding_summary(analysis) -> None:
    print("Guided onboarding summary")
    print(f"  Database target: {analysis.database_target} (password hidden)")
    print(f"  Included tables ({len(analysis.included_tables)}): {_preview_values(analysis.included_tables, limit=12)}")
    print(f"  Excluded tables ({len(analysis.excluded_tables)}): {_preview_values(analysis.excluded_tables, limit=12)}")
    review_report = (analysis.artifacts.review_report if analysis.artifacts is not None else {}) or {}
    inference_summary = review_report.get("inference_summary") or {}
    primary = ((inference_summary.get("primary_table") or {}).get("value")) if isinstance(inference_summary, dict) else ""
    user_table = ((inference_summary.get("user_table") or {}).get("value")) if isinstance(inference_summary, dict) else ""
    location_table = ((inference_summary.get("location_table") or {}).get("value")) if isinstance(inference_summary, dict) else ""
    if primary:
        print(f"  Primary table candidate: {primary}")
    if user_table:
        print(f"  User table candidate: {user_table}")
    if location_table:
        print(f"  Location table candidate: {location_table}")
    if analysis.clarification_questions:
        print("  Review flags:")
        for question in analysis.clarification_questions[:4]:
            print(f"    - {question.question} Recommended: {question.recommended_answer}")


def _ask_question(question: ClarificationQuestion, *, index: int, total: int) -> Any:
    print()
    print(f"[{index}/{total}] {question.prompt}")
    if question.help_text:
        print(f"    {question.help_text}")
    if question.options:
        print(f"    Options: {', '.join(question.options)}")
    default_text = _default_text(question)
    suffix = ""
    if default_text and question.allow_blank:
        suffix = f" [{default_text}; type 'none' to clear]"
    elif default_text:
        suffix = f" [{default_text}]"
    elif question.allow_blank:
        suffix = " [optional; type 'none' to clear]"

    raw = input(f"    Answer{suffix}: ").strip()
    lowered = raw.lower()
    if question.allow_blank and lowered in {"none", "null", "clear", "n/a"}:
        return [] if question.multi_value else ""
    if not raw:
        if question.multi_value and isinstance(question.default_value, list):
            return list(question.default_value)
        return default_text
    if question.multi_value:
        return [item.strip() for item in raw.split(",") if item.strip()]
    return raw


def _collect_answers(questions: List[ClarificationQuestion]) -> Dict[str, Any]:
    answers: Dict[str, Any] = {}
    total = len(questions)
    for index, question in enumerate(questions, start=1):
        answers[question.key] = _ask_question(question, index=index, total=total)
    return answers


def _effective_db_url(explicit_db_url: str, *, simple_mode: bool) -> str | None:
    explicit = str(explicit_db_url or "").strip()
    if explicit:
        return explicit
    if simple_mode:
        docker_url = str(os.getenv("DATABASE_URL_DOCKER") or "").strip()
        if docker_url:
            return docker_url
    return None


def main(argv: List[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    config_path = _config_path_from_args(args)
    if bool(args.generate_config):
        if config_path is None:
            raise SystemExit("error: unable to determine config file path")
        config_payload = _build_run_config_payload(args, config_path=config_path)
        _write_json(config_path, config_payload)
        print(f"Generator config written to: {config_path.as_posix()}")
        print()
        print("Next steps:")
        print(f"  1. Open {config_path.as_posix()} in your IDE")
        print("  2. Fill in request.domain, request.db_url, request.app_name, and any metadata hints you need")
        print(f"  3. Run: python scripts/generate_domain.py --config-file {config_path.as_posix()}")
        return 0

    config_payload: Dict[str, Any] | None = None
    if config_path is not None:
        config_payload = _load_run_config(config_path)
        if not str(args.config_file or "").strip():
            print(f"Using config file: {config_path.as_posix()}")
    args = _apply_run_config(args, config_payload, config_path=config_path)
    if not str(args.domain or "").strip():
        if config_path is not None and config_payload is not None:
            config_payload = _write_run_config_status(
                config_path,
                config_payload,
                state="failed",
                message="Generation failed: request.domain is required.",
                finished_at=_now_iso(),
                result={"error": "request.domain is required"},
            )
        parser.error("--domain is required unless provided in --config-file")

    run_started_at: str | None = None
    if config_path is not None and config_payload is not None:
        run_started_at = _now_iso()
        config_payload = _write_run_config_status(
            config_path,
            config_payload,
            state="running",
            message="Domain generation started.",
            started_at=run_started_at,
            result={},
        )

    try:
        service = DomainGenerationService()
        onboarding_service = DomainOnboardingService(generator=service)
        simple_mode = bool(args.simple)
        guided_mode = bool(args.guided)
        interactive_prompts = bool(getattr(args, "interactive_prompts", False))
        if simple_mode and interactive_prompts:
            args.developer_clarifications = True
        if guided_mode and interactive_prompts:
            args.developer_clarifications = True
        output_root = Path(args.output_root)
        domain_dir = output_root / str(args.domain or "").strip().lower().replace(" ", "_")
        inline_metadata_hints = _request_object(config_payload, "metadata_hints")
        inline_clarification_hints = _request_object(config_payload, "clarification_hints")

        metadata_file = Path(str(args.metadata_file or "").strip()) if str(args.metadata_file or "").strip() else None
        metadata_hints = _load_optional_json(metadata_file)
        if inline_metadata_hints:
            metadata_hints = service.merge_metadata_hints(metadata_hints, inline_metadata_hints)

        clarification_file = Path(str(args.clarification_file or "").strip()) if str(args.clarification_file or "").strip() else None
        persisted_clarifications_path = clarification_file or (domain_dir / "developer_clarifications.json")
        developer_clarifications = _load_optional_json(persisted_clarifications_path)
        if inline_clarification_hints:
            developer_clarifications = service.merge_metadata_hints(developer_clarifications, inline_clarification_hints)

        combined_hints = service.merge_metadata_hints(metadata_hints, developer_clarifications)
        combined_hints = _merge_table_hints(
            service,
            combined_hints,
            include_tables=_clean_list(list(args.include_table or [])) or None,
            exclude_tables=_clean_list(list(args.exclude_table or [])) or None,
        )
        initial_table_hints: Dict[str, Any] = {}
        if _table_hint_values(combined_hints, "include_tables"):
            initial_table_hints["include_tables"] = _table_hint_values(combined_hints, "include_tables")
        if _table_hint_values(combined_hints, "exclude_tables"):
            initial_table_hints["exclude_tables"] = _table_hint_values(combined_hints, "exclude_tables")
        if initial_table_hints:
            developer_clarifications = service.merge_metadata_hints(developer_clarifications, initial_table_hints)

        effective_db_url = _effective_db_url(str(args.db_url or "").strip(), simple_mode=simple_mode)
        if simple_mode and not str(args.db_url or "").strip() and str(os.getenv("DATABASE_URL_DOCKER") or "").strip():
            print("Simple mode: using DATABASE_URL_DOCKER from the environment.")
        snapshot = service.introspect_schema(db_url=effective_db_url)
        active_snapshot = snapshot
        onboarding_analysis = None

        if guided_mode:
            initial_include_tables = _table_hint_values(combined_hints, "include_tables")
            initial_exclude_tables = _table_hint_values(combined_hints, "exclude_tables")
            onboarding_analysis = onboarding_service.analyze_snapshot(
                domain_name=str(args.domain or "").strip(),
                schema_snapshot=snapshot,
                description=str(args.description or "").strip(),
                metadata_hints=combined_hints,
                include_tables=initial_include_tables,
                exclude_tables=initial_exclude_tables,
                connection_source=_connection_source(str(args.db_url or "").strip(), simple_mode=simple_mode),
                database_target=str(snapshot.get("database_target") or ""),
            )
            _print_onboarding_summary(onboarding_analysis)
            if interactive_prompts:
                print()
                reviewed_include_tables = _ask_list(
                    "Force-include any additional tables",
                    default_values=initial_include_tables,
                )
                reviewed_exclude_tables = _ask_list(
                    "Exclude any tables from generation",
                    default_values=initial_exclude_tables,
                )
                combined_hints = _merge_table_hints(
                    service,
                    combined_hints,
                    include_tables=reviewed_include_tables,
                    exclude_tables=reviewed_exclude_tables,
                )
                developer_clarifications = service.merge_metadata_hints(
                    developer_clarifications,
                    {
                        "include_tables": reviewed_include_tables,
                        "exclude_tables": reviewed_exclude_tables,
                    },
                )
                onboarding_analysis = onboarding_service.analyze_snapshot(
                    domain_name=str(args.domain or "").strip(),
                    schema_snapshot=snapshot,
                    description=str(args.description or "").strip(),
                    metadata_hints=combined_hints,
                    include_tables=reviewed_include_tables,
                    exclude_tables=reviewed_exclude_tables,
                    connection_source=_connection_source(str(args.db_url or "").strip(), simple_mode=simple_mode),
                    database_target=str(snapshot.get("database_target") or ""),
                )
                print()
                _print_onboarding_summary(onboarding_analysis)
            else:
                auto_include_tables = list(onboarding_analysis.included_tables)
                auto_exclude_tables = list(onboarding_analysis.excluded_tables)
                combined_hints = _merge_table_hints(
                    service,
                    combined_hints,
                    include_tables=auto_include_tables,
                    exclude_tables=auto_exclude_tables,
                )
                developer_clarifications = service.merge_metadata_hints(
                    developer_clarifications,
                    {
                        "include_tables": auto_include_tables,
                        "exclude_tables": auto_exclude_tables,
                    },
                )
                print("Auto mode: applying guided table recommendations from the JSON/non-interactive workflow.")
            active_snapshot = _filter_snapshot(snapshot, onboarding_analysis.included_tables)
        elif _table_hint_values(combined_hints, "include_tables") or _table_hint_values(combined_hints, "exclude_tables"):
            filtered_artifacts = service.build_artifacts(
                str(args.domain or "").strip(),
                snapshot,
                description=str(args.description or "").strip(),
                metadata_hints=combined_hints,
            )
            active_snapshot = _filter_snapshot(
                snapshot,
                list((filtered_artifacts.manifest_payload().get("tables") or {}).keys()),
            )

        artifacts = service.build_artifacts(
            str(args.domain or "").strip(),
            active_snapshot,
            description=str(args.description or "").strip(),
            metadata_hints=combined_hints,
        )
    except Exception as exc:
        if config_path is not None and config_payload is not None:
            config_payload = _write_run_config_status(
                config_path,
                config_payload,
                state="failed",
                message=f"Generation failed: {exc}",
                started_at=run_started_at,
                finished_at=_now_iso(),
                result={"error": str(exc)},
            )
        raise

    try:
        # --generate-template: write a JSON template and exit
        if bool(getattr(args, "generate_template", False)):
            template = service.build_semantics_template(
                active_snapshot,
                artifacts,
                metadata_hints=combined_hints,
            )
            template_path = domain_dir / "semantics_template.json"
            _write_json(template_path, template)
            if config_path is not None and config_payload is not None:
                config_payload = _write_run_config_status(
                    config_path,
                    config_payload,
                    state="template_generated",
                    message=(
                        "Semantics template written. Fill the _TODO fields, set completed=true, "
                        "then rerun the generator."
                    ),
                    started_at=run_started_at,
                    finished_at=_now_iso(),
                    result={
                        "mode": "generate_template",
                        "domain": str(args.domain or "").strip(),
                        "semantics_template": template_path.as_posix(),
                    },
                )
            print(f"Semantics template written to: {template_path.as_posix()}")
            print()
            print("Next steps:")
            print(f"  1. Open {template_path.as_posix()} in your IDE")
            print("  2. Fill in the _TODO fields with your domain knowledge")
            print('  3. Set "completed": true')
            print(f"  4. Re-run with: --clarification-file {template_path.as_posix()}")
            return 0

        # Load completed semantics template if provided via --clarification-file
        if clarification_file and clarification_file.exists():
            template_hints = _load_template_as_hints(clarification_file)
            if template_hints:
                developer_clarifications = service.merge_metadata_hints(developer_clarifications, template_hints)
                combined_hints = service.merge_metadata_hints(metadata_hints, developer_clarifications)
                artifacts = service.build_artifacts(
                    str(args.domain or "").strip(),
                    active_snapshot,
                    description=str(args.description or "").strip(),
                    metadata_hints=combined_hints,
                )

        asked_questions: List[ClarificationQuestion] = []
        answered_keys: List[str] = []
        if bool(args.developer_clarifications) and interactive_prompts:
            context_questions = service.build_clarification_questions(
                active_snapshot,
                artifacts,
                metadata_hints=combined_hints,
                phase="context",
            )
            if context_questions:
                print("Developer clarification pass 1/4: app context")
                context_answers = _collect_answers(context_questions)
                context_hints = service.clarification_hints_from_answers(context_questions, context_answers)
                if context_hints:
                    developer_clarifications = service.merge_metadata_hints(developer_clarifications, context_hints)
                    combined_hints = service.merge_metadata_hints(metadata_hints, developer_clarifications)
                    combined_hints = _merge_table_hints(
                        service,
                        combined_hints,
                        include_tables=_table_hint_values(combined_hints, "include_tables") or None,
                        exclude_tables=_table_hint_values(combined_hints, "exclude_tables") or None,
                    )
                    artifacts = service.build_artifacts(
                        str(args.domain or "").strip(),
                        active_snapshot,
                        description=str(args.description or "").strip(),
                        metadata_hints=combined_hints,
                    )
                    answered_keys.extend(sorted(context_answers.keys()))
                asked_questions.extend(context_questions)

            role_questions = service.build_clarification_questions(
                active_snapshot,
                artifacts,
                metadata_hints=combined_hints,
                phase="roles",
            )
            if role_questions:
                print()
                print("Developer clarification pass 2/4: table roles")
                role_answers = _collect_answers(role_questions)
                role_hints = service.clarification_hints_from_answers(role_questions, role_answers)
                if role_hints:
                    developer_clarifications = service.merge_metadata_hints(developer_clarifications, role_hints)
                    combined_hints = service.merge_metadata_hints(metadata_hints, developer_clarifications)
                    combined_hints = _merge_table_hints(
                        service,
                        combined_hints,
                        include_tables=_table_hint_values(combined_hints, "include_tables") or None,
                        exclude_tables=_table_hint_values(combined_hints, "exclude_tables") or None,
                    )
                    artifacts = service.build_artifacts(
                        str(args.domain or "").strip(),
                        active_snapshot,
                        description=str(args.description or "").strip(),
                        metadata_hints=combined_hints,
                    )
                    answered_keys.extend(sorted(role_answers.keys()))
                asked_questions.extend(role_questions)

            detail_questions = service.build_clarification_questions(
                active_snapshot,
                artifacts,
                metadata_hints=combined_hints,
                phase="details",
            )
            if simple_mode or guided_mode:
                detail_questions = [
                    question
                    for question in detail_questions
                    if question.key.startswith("entities.")
                ]
            if detail_questions:
                print()
                if simple_mode or guided_mode:
                    print("Developer clarification pass 3/4: table meaning and labels")
                else:
                    print("Developer clarification pass 3/4: semantics and important columns")
                detail_answers = _collect_answers(detail_questions)
                detail_hints = service.clarification_hints_from_answers(detail_questions, detail_answers)
                if detail_hints:
                    developer_clarifications = service.merge_metadata_hints(developer_clarifications, detail_hints)
                    combined_hints = service.merge_metadata_hints(metadata_hints, developer_clarifications)
                    combined_hints = _merge_table_hints(
                        service,
                        combined_hints,
                        include_tables=_table_hint_values(combined_hints, "include_tables") or None,
                        exclude_tables=_table_hint_values(combined_hints, "exclude_tables") or None,
                    )
                    artifacts = service.build_artifacts(
                        str(args.domain or "").strip(),
                        active_snapshot,
                        description=str(args.description or "").strip(),
                        metadata_hints=combined_hints,
                    )
                    answered_keys.extend(sorted(detail_answers.keys()))
                asked_questions.extend(detail_questions)

            # Phase 4: semantics — enum values, column business descriptions, business terms
            semantic_questions = service.build_clarification_questions(
                active_snapshot,
                artifacts,
                metadata_hints=combined_hints,
                phase="semantics",
            )
            if semantic_questions:
                print()
                if simple_mode or guided_mode:
                    print("Developer clarification pass 4/4: column semantics and enum values")
                else:
                    print("Developer clarification pass 4/4: enum values, column meaning, and business terms")
                semantic_answers = _collect_answers(semantic_questions)
                semantic_hints = service.clarification_hints_from_answers(semantic_questions, semantic_answers)
                if semantic_hints:
                    developer_clarifications = service.merge_metadata_hints(developer_clarifications, semantic_hints)
                    combined_hints = service.merge_metadata_hints(metadata_hints, developer_clarifications)
                    combined_hints = _merge_table_hints(
                        service,
                        combined_hints,
                        include_tables=_table_hint_values(combined_hints, "include_tables") or None,
                        exclude_tables=_table_hint_values(combined_hints, "exclude_tables") or None,
                    )
                    artifacts = service.build_artifacts(
                        str(args.domain or "").strip(),
                        active_snapshot,
                        description=str(args.description or "").strip(),
                        metadata_hints=combined_hints,
                    )
                    answered_keys.extend(sorted(semantic_answers.keys()))
                asked_questions.extend(semantic_questions)

        if developer_clarifications:
            artifacts.root_json_files["developer_clarifications.json"] = developer_clarifications
            artifacts.review_report["developer_clarifications"] = {
                "question_count": len(asked_questions),
                "answered_keys": sorted(set(answered_keys)),
                "source_file": (
                    clarification_file.as_posix()
                    if clarification_file is not None
                    else config_path.as_posix()
                    if inline_clarification_hints and config_path is not None
                    else persisted_clarifications_path.as_posix()
                ),
            }
            artifacts.root_json_files["review_report.json"] = artifacts.review_report

        artifacts = service.write_artifacts(
            artifacts,
            output_root=output_root,
            force=bool(args.force),
        )
        onboarding_report_path = None
        if onboarding_analysis is not None:
            onboarding_analysis.artifacts = artifacts
            onboarding_report_path = onboarding_service.write_analysis_report(
                onboarding_analysis,
                output_root / artifacts.domain_name / "onboarding_report.json",
            )
        needs_review = artifacts.review_report.get("needs_review") or []
        review_report_path = Path(args.output_root) / artifacts.domain_name / "review_report.json"
        developer_clarifications_path = (
            (Path(args.output_root) / artifacts.domain_name / "developer_clarifications.json")
            if developer_clarifications
            else None
        )
        if config_path is not None and config_payload is not None:
            result_payload = {
                "mode": "generate_domain",
                "domain": artifacts.domain_name,
                "domain_dir": (Path(args.output_root) / artifacts.domain_name).as_posix(),
                "written_files": len(artifacts.written_files),
                "needs_review": len(needs_review),
                "review_report": review_report_path.as_posix(),
            }
            if onboarding_report_path is not None:
                result_payload["onboarding_report"] = onboarding_report_path.as_posix()
            if developer_clarifications_path is not None:
                result_payload["developer_clarifications"] = developer_clarifications_path.as_posix()
            config_payload = _write_run_config_status(
                config_path,
                config_payload,
                state="completed",
                message="Domain package generated successfully.",
                started_at=run_started_at,
                finished_at=_now_iso(),
                result=result_payload,
            )
        print(f"Generated domain `{artifacts.domain_name}`")
        print(f"Files written: {len(artifacts.written_files)}")
        print(f"Needs review: {len(needs_review)}")
        print(f"Review report: {review_report_path.as_posix()}")
        if onboarding_report_path is not None:
            print(f"Onboarding report: {onboarding_report_path.as_posix()}")
        if developer_clarifications_path is not None:
            print(f"Developer clarifications: {developer_clarifications_path.as_posix()}")
        return 0
    except Exception as exc:
        if config_path is not None and config_payload is not None:
            config_payload = _write_run_config_status(
                config_path,
                config_payload,
                state="failed",
                message=f"Generation failed: {exc}",
                started_at=run_started_at,
                finished_at=_now_iso(),
                result={"error": str(exc)},
            )
        raise


if __name__ == "__main__":
    raise SystemExit(main())
