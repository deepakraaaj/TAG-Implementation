#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any, Dict, List

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.config import get_settings
from openai import AsyncOpenAI

DEFAULT_OUTPUT_ROOT = "domains"
RUN_CONFIG_VERSION = 1
DEFAULT_RUN_CONFIG_PATH = REPO_ROOT / "scripts" / "onboard_domain.request.json"


class _OpenAIChatAdapter:
    def __init__(self, client: AsyncOpenAI, *, model: str) -> None:
        self._client = client
        self._model = model

    async def ainvoke(self, prompt: str, max_tokens: int | None = None) -> str:
        response = await self._client.chat.completions.create(
            model=self._model,
            temperature=0,
            max_tokens=max_tokens,
            messages=[
                {"role": "system", "content": "Return concise, valid JSON when asked."},
                {"role": "user", "content": prompt},
            ],
        )
        choice = response.choices[0] if response.choices else None
        message = getattr(choice, "message", None)
        content = getattr(message, "content", "")
        if isinstance(content, list):
            parts: List[str] = []
            for item in content:
                if isinstance(item, dict):
                    text = item.get("text")
                else:
                    text = getattr(item, "text", None)
                if text:
                    parts.append(str(text).strip())
            return "\n".join(part for part in parts if part).strip()
        return str(content or "").strip()


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Analyze a live database and generate a domain onboarding report with clarification questions.",
    )
    parser.add_argument(
        "--config-file",
        default="",
        help="Optional JSON config file for onboarding. CLI flags override values loaded from this file.",
    )
    parser.add_argument(
        "--generate-config",
        action="store_true",
        help="Write a reusable JSON onboarding config template and exit.",
    )
    parser.add_argument("--domain", default=None, help="Domain name to analyze or generate.")
    parser.add_argument(
        "--db-url",
        default=None,
        help="Database URL to inspect. Defaults to the configured DATABASE_URL.",
    )
    parser.add_argument(
        "--description",
        default=None,
        help="Optional domain description override.",
    )
    parser.add_argument(
        "--metadata-file",
        default=None,
        help="Optional JSON file with business vocabulary, examples, and workflow hints.",
    )
    parser.add_argument(
        "--include-table",
        action="append",
        default=None,
        help="Force-include a table even if the onboarding heuristics would exclude it. Repeatable.",
    )
    parser.add_argument(
        "--exclude-table",
        action="append",
        default=None,
        help="Force-exclude a table. Repeatable.",
    )
    parser.add_argument("--primary-table", default=None, help="Explicit primary business table override.")
    parser.add_argument("--user-table", default=None, help="Explicit people/user table override.")
    parser.add_argument("--location-table", default=None, help="Explicit facility/location table override.")
    parser.add_argument(
        "--output-root",
        default=None,
        help="Root folder where the generated domain package should be written when --write is set.",
    )
    parser.add_argument(
        "--report-file",
        default=None,
        help="Optional path to write the onboarding report JSON.",
    )
    parser.add_argument(
        "--write",
        action="store_true",
        default=None,
        help="Write the generated domain package and onboarding report after analysis.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        default=None,
        help="Overwrite an existing generated domain package when used with --write.",
    )
    parser.add_argument(
        "--enable-llm-enhancement",
        action="store_true",
        default=None,
        help="Use LLM to enhance generated metadata with descriptions, aliases, and examples.",
    )
    return parser


def parse_args(argv: List[str] | None = None) -> argparse.Namespace:
    return _build_parser().parse_args(argv)


def _load_json_dict(path: Path) -> Dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    return dict(payload) if isinstance(payload, dict) else {}


def _write_json(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=False)
        handle.write("\n")


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


def _clean_list(values: Any) -> List[str]:
    if not isinstance(values, list):
        return []
    cleaned: List[str] = []
    for value in values:
        text = _string_value(value)
        if text and text not in cleaned:
            cleaned.append(text)
    return cleaned


def _merge_dicts(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    merged = dict(base or {})
    for key, value in (override or {}).items():
        if key in merged and isinstance(merged[key], dict) and isinstance(value, dict):
            merged[key] = _merge_dicts(merged[key], value)
        else:
            merged[key] = value
    return merged


def _config_path_from_args(args: argparse.Namespace) -> Path | None:
    raw_config_path = _string_value(getattr(args, "config_file", None))
    if raw_config_path:
        return Path(raw_config_path)
    if bool(getattr(args, "generate_config", False)):
        return DEFAULT_RUN_CONFIG_PATH
    if DEFAULT_RUN_CONFIG_PATH.exists():
        return DEFAULT_RUN_CONFIG_PATH
    return None


def _build_run_config_payload(args: argparse.Namespace, *, config_path: Path) -> Dict[str, Any]:
    command_example = f"python scripts/onboard_domain.py --config-file {config_path.as_posix()}"
    return {
        "_instructions": [
            "Fill request values below, then rerun the onboarding script with this config file.",
            "CLI flags override values loaded from request.",
            "Put business context directly in request.metadata_hints so you do not need a separate metadata JSON file.",
        ],
        "_chatgpt_prompt": [
            "Act as a TAG domain onboarding assistant.",
            "Help the user complete this JSON for scripts/onboard_domain.py.",
            "Ask short questions one at a time and wait for the user's answer before asking the next question.",
            "Collect at least request.domain, request.db_url, request.description or request.metadata_hints.scope, and 2-4 request.metadata_hints.example_queries.",
            "Also ask for likely primary, user, and location tables, include/exclude tables, business terms, and entity labels if the user knows them.",
            "Do not invent database URLs, table names, or business facts. Leave unknown fields empty.",
            "When enough information is collected, reply with the completed JSON only in a json code block.",
            "Keep all existing keys and preserve valid JSON syntax.",
        ],
        "version": RUN_CONFIG_VERSION,
        "request": {
            "domain": _string_value(getattr(args, "domain", None)) or "your_domain",
            "db_url": _string_value(getattr(args, "db_url", None)),
            "description": _string_value(getattr(args, "description", None)),
            "metadata_file": _string_value(getattr(args, "metadata_file", None)),
            "output_root": _string_value(getattr(args, "output_root", None)) or DEFAULT_OUTPUT_ROOT,
            "report_file": _string_value(getattr(args, "report_file", None)),
            "write": _bool_value(getattr(args, "write", None), default=True),
            "force": _bool_value(getattr(args, "force", None)),
            "enable_llm_enhancement": _bool_value(getattr(args, "enable_llm_enhancement", None)),
            "include_tables": _clean_list(getattr(args, "include_table", None)),
            "exclude_tables": _clean_list(getattr(args, "exclude_table", None)),
            "primary_table": _string_value(getattr(args, "primary_table", None)),
            "user_table": _string_value(getattr(args, "user_table", None)),
            "location_table": _string_value(getattr(args, "location_table", None)),
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
        },
    }


def _load_run_config(path: Path) -> Dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"Config file not found: {path.as_posix()}")
    payload = _load_json_dict(path)
    if not payload:
        return {"request": {}}
    request = payload.get("request")
    if request is None:
        return {"request": dict(payload)}
    if not isinstance(request, dict):
        raise ValueError("Onboarding config file must contain an object at request.")
    return dict(payload)


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


def _resolve_list_arg(cli_value: Any, config_request: Dict[str, Any], key: str) -> List[str]:
    if cli_value is not None:
        return _clean_list(cli_value)
    return _clean_list(config_request.get(key))


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


def _resolve_metadata(path_value: str) -> Dict[str, Any]:
    metadata_file = Path(_string_value(path_value)) if _string_value(path_value) else None
    if metadata_file is None:
        return {}
    return _load_json_dict(metadata_file)


def _resolve_metadata_hints(
    *,
    metadata_file: str,
    config_request: Dict[str, Any],
) -> Dict[str, Any]:
    file_hints = _resolve_metadata(metadata_file)
    inline_hints = config_request.get("metadata_hints")
    if not isinstance(inline_hints, dict):
        inline_hints = {}
    return _merge_dicts(file_hints, inline_hints)


def _apply_run_config(
    args: argparse.Namespace,
    config_payload: Dict[str, Any] | None,
    *,
    config_path: Path | None = None,
) -> argparse.Namespace:
    request = dict((config_payload or {}).get("request") or {})
    resolved = argparse.Namespace(**vars(args))
    resolved.domain = _resolve_string_arg(args.domain, request, "domain")
    resolved.db_url = _resolve_string_arg(args.db_url, request, "db_url")
    resolved.description = _resolve_string_arg(args.description, request, "description")
    resolved.metadata_file = _resolve_string_arg(
        args.metadata_file,
        request,
        "metadata_file",
        config_path=config_path,
        resolve_relative_to_config=True,
    )
    resolved.include_table = _resolve_list_arg(args.include_table, request, "include_tables")
    resolved.exclude_table = _resolve_list_arg(args.exclude_table, request, "exclude_tables")
    resolved.primary_table = _resolve_string_arg(args.primary_table, request, "primary_table")
    resolved.user_table = _resolve_string_arg(args.user_table, request, "user_table")
    resolved.location_table = _resolve_string_arg(args.location_table, request, "location_table")
    resolved.output_root = _resolve_string_arg(args.output_root, request, "output_root", default=DEFAULT_OUTPUT_ROOT)
    resolved.report_file = _resolve_string_arg(
        args.report_file,
        request,
        "report_file",
        config_path=config_path,
        resolve_relative_to_config=True,
    )
    resolved.write = _resolve_bool_arg(args.write, request, "write")
    resolved.force = _resolve_bool_arg(args.force, request, "force")
    resolved.enable_llm_enhancement = _resolve_bool_arg(
        args.enable_llm_enhancement,
        request,
        "enable_llm_enhancement",
    )
    resolved.metadata_hints = _resolve_metadata_hints(
        metadata_file=resolved.metadata_file,
        config_request=request,
    )
    return resolved


def _print_analysis_summary(analysis) -> None:
    print(f"Domain: {analysis.domain_name}")
    print(f"Database target: {analysis.database_target} (password hidden)")
    print(f"Connection source: {analysis.connection_source}")
    print(f"Included tables: {len(analysis.included_tables)}")
    if analysis.included_tables:
        print("  " + ", ".join(analysis.included_tables[:12]))
    print(f"Excluded tables: {len(analysis.excluded_tables)}")
    if analysis.excluded_tables:
        print("  " + ", ".join(analysis.excluded_tables[:12]))

    review_summary = (analysis.artifacts.review_report if analysis.artifacts is not None else {}).get("inference_summary", {})
    primary = (((review_summary.get("primary_table") or {}).get("value")) if isinstance(review_summary, dict) else "") or ""
    if primary:
        print(f"Primary table candidate: {primary}")

    print("Clarification questions:")
    if not analysis.clarification_questions:
        print("  none")
        return
    for index, question in enumerate(analysis.clarification_questions, start=1):
        print(f"  {index}. {question.question}")
        print(f"     Recommended answer: {question.recommended_answer}")
        if question.context:
            print(f"     Context: {question.context}")


async def main(argv: List[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    from tools.domain_onboarding import DomainOnboardingService

    config_path = _config_path_from_args(args)
    if args.generate_config:
        resolved_config_path = config_path or DEFAULT_RUN_CONFIG_PATH
        payload = _build_run_config_payload(args, config_path=resolved_config_path)
        _write_json(resolved_config_path, payload)
        print(f"Wrote onboarding config template: {resolved_config_path.as_posix()}")
        print(f"Run with: python scripts/onboard_domain.py --config-file {resolved_config_path.as_posix()}")
        return 0

    config_payload = None
    if config_path is not None:
        try:
            config_payload = _load_run_config(config_path)
        except Exception as exc:
            print(f"Failed to load onboarding config: {exc}", file=sys.stderr)
            return 1

    args = _apply_run_config(args, config_payload, config_path=config_path)
    if not _string_value(args.domain):
        parser.error("--domain is required unless provided in --config-file")

    llm_client = None
    if args.enable_llm_enhancement:
        settings = get_settings()
        llm_client = _OpenAIChatAdapter(
            AsyncOpenAI(
                api_key=settings.LLM_API_KEY,
                base_url=settings.LLM_BASE_URL,
            ),
            model=settings.LLM_MODEL,
        )

    service = DomainOnboardingService(llm_client=llm_client)
    try:
        analysis = await service.aanalyze(
            domain_name=args.domain,
            db_url=_string_value(args.db_url) or None,
            description=_string_value(args.description),
            metadata_hints=dict(getattr(args, "metadata_hints", {}) or {}),
            include_tables=list(args.include_table or []),
            exclude_tables=list(args.exclude_table or []),
            primary_table=_string_value(args.primary_table),
            user_table=_string_value(args.user_table),
            location_table=_string_value(args.location_table),
            enable_llm_enhancement=bool(args.enable_llm_enhancement),
        )
    except Exception as exc:
        print(f"Domain onboarding failed: {exc}", file=sys.stderr)
        lowered = str(exc).lower()
        if any(token in lowered for token in ("can't connect", "connection refused", "timed out", "unknown mysql server host")):
            print(
                "Check DB_URL or DATABASE_URL, verify the database is reachable from this machine, and confirm credentials are valid.",
                file=sys.stderr,
            )
        return 1

    _print_analysis_summary(analysis)

    report_path = Path(_string_value(args.report_file)) if _string_value(args.report_file) else None
    if args.write:
        try:
            artifacts = service.write_domain(
                analysis,
                output_root=Path(args.output_root),
                force=bool(args.force),
            )
        except Exception as exc:
            print(f"Failed to write generated domain: {exc}", file=sys.stderr)
            return 1
        auto_report_path = Path(args.output_root) / artifacts.domain_name / "onboarding_report.json"
        service.write_analysis_report(analysis, auto_report_path)
        print(f"Generated domain `{artifacts.domain_name}`")
        print(f"Files written: {len(artifacts.written_files)}")
        print(f"Onboarding report: {auto_report_path.as_posix()}")
    elif report_path is not None:
        service.write_analysis_report(analysis, report_path)
        print(f"Onboarding report: {report_path.as_posix()}")

    return 0


if __name__ == "__main__":
    import asyncio

    raise SystemExit(asyncio.run(main()))
