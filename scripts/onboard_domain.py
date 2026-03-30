#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Analyze a live database and generate a domain onboarding report with clarification questions.",
    )
    parser.add_argument("--domain", required=True, help="Domain name to analyze or generate.")
    parser.add_argument(
        "--db-url",
        default="",
        help="Database URL to inspect. Defaults to the configured DATABASE_URL.",
    )
    parser.add_argument(
        "--description",
        default="",
        help="Optional domain description override.",
    )
    parser.add_argument(
        "--metadata-file",
        default="",
        help="Optional JSON file with business vocabulary, examples, and workflow hints.",
    )
    parser.add_argument(
        "--include-table",
        action="append",
        default=[],
        help="Force-include a table even if the onboarding heuristics would exclude it. Repeatable.",
    )
    parser.add_argument(
        "--exclude-table",
        action="append",
        default=[],
        help="Force-exclude a table. Repeatable.",
    )
    parser.add_argument("--primary-table", default="", help="Explicit primary business table override.")
    parser.add_argument("--user-table", default="", help="Explicit people/user table override.")
    parser.add_argument("--location-table", default="", help="Explicit facility/location table override.")
    parser.add_argument(
        "--output-root",
        default="app/domains",
        help="Root folder where the generated domain package should be written when --write is set.",
    )
    parser.add_argument(
        "--report-file",
        default="",
        help="Optional path to write the onboarding report JSON.",
    )
    parser.add_argument(
        "--write",
        action="store_true",
        help="Write the generated domain package and onboarding report after analysis.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite known generated files if the output domain folder already exists.",
    )
    return parser.parse_args()


def _load_metadata(path_value: str) -> dict:
    metadata_file = Path(str(path_value or "").strip()) if str(path_value or "").strip() else None
    if metadata_file is None:
        return {}
    with metadata_file.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    return dict(payload) if isinstance(payload, dict) else {}


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


def main() -> int:
    args = parse_args()
    from tools.domain_onboarding import DomainOnboardingService

    metadata_hints = _load_metadata(args.metadata_file)
    service = DomainOnboardingService()
    analysis = service.analyze(
        domain_name=args.domain,
        db_url=str(args.db_url or "").strip() or None,
        description=str(args.description or "").strip(),
        metadata_hints=metadata_hints,
        include_tables=list(args.include_table or []),
        exclude_tables=list(args.exclude_table or []),
        primary_table=str(args.primary_table or "").strip(),
        user_table=str(args.user_table or "").strip(),
        location_table=str(args.location_table or "").strip(),
    )
    _print_analysis_summary(analysis)

    report_path = Path(str(args.report_file or "").strip()) if str(args.report_file or "").strip() else None
    if args.write:
        artifacts = service.write_domain(
            analysis,
            output_root=Path(args.output_root),
            force=bool(args.force),
        )
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
    raise SystemExit(main())
