#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
from typing import Any, Dict, List

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.domains.generator import ClarificationQuestion, DomainGenerationService


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate a domain package from a live database schema.",
    )
    parser.add_argument(
        "--domain",
        required=True,
        help="Domain name to generate, for example: maintenance_v2",
    )
    parser.add_argument(
        "--db-url",
        default="",
        help="Database URL to inspect. Defaults to DATABASE_URL, or DATABASE_URL_DOCKER in --simple mode.",
    )
    parser.add_argument(
        "--description",
        default="",
        help="Optional domain description override.",
    )
    parser.add_argument(
        "--output-root",
        default="app/domains",
        help="Root folder where the domain package should be written.",
    )
    parser.add_argument(
        "--metadata-file",
        default="",
        help="Optional JSON file with project vocabulary, examples, and workflow hints.",
    )
    parser.add_argument(
        "--clarification-file",
        default="",
        help="Optional JSON file with previously approved developer clarifications.",
    )
    parser.add_argument(
        "--developer-clarifications",
        action="store_true",
        help="Ask the developer targeted clarification questions before writing the domain package.",
    )
    parser.add_argument(
        "--simple",
        action="store_true",
        help="Simple mode: prefer DATABASE_URL_DOCKER when available and ask only the minimal table-meaning questions.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite known generated files if the domain folder already exists.",
    )
    return parser.parse_args()


def _load_optional_json(path: Path | None) -> Dict[str, Any]:
    if path is None or not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    return dict(payload) if isinstance(payload, dict) else {}


def _default_text(question: ClarificationQuestion) -> str:
    value = question.default_value
    if isinstance(value, list):
        return ", ".join(str(item or "").strip() for item in value if str(item or "").strip())
    return str(value or "").strip()


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


def main() -> int:
    args = parse_args()
    service = DomainGenerationService()
    simple_mode = bool(args.simple)
    if simple_mode:
        args.developer_clarifications = True
    output_root = Path(args.output_root)
    domain_dir = output_root / str(args.domain or "").strip().lower().replace(" ", "_")

    metadata_file = Path(str(args.metadata_file or "").strip()) if str(args.metadata_file or "").strip() else None
    metadata_hints = _load_optional_json(metadata_file)

    clarification_file = Path(str(args.clarification_file or "").strip()) if str(args.clarification_file or "").strip() else None
    persisted_clarifications_path = clarification_file or (domain_dir / "developer_clarifications.json")
    developer_clarifications = _load_optional_json(persisted_clarifications_path)

    combined_hints = service.merge_metadata_hints(metadata_hints, developer_clarifications)
    effective_db_url = _effective_db_url(str(args.db_url or "").strip(), simple_mode=simple_mode)
    if simple_mode and not str(args.db_url or "").strip() and str(os.getenv("DATABASE_URL_DOCKER") or "").strip():
        print("Simple mode: using DATABASE_URL_DOCKER from the environment.")
    snapshot = service.introspect_schema(db_url=effective_db_url)
    artifacts = service.build_artifacts(
        str(args.domain or "").strip(),
        snapshot,
        description=str(args.description or "").strip(),
        metadata_hints=combined_hints,
    )

    asked_questions: List[ClarificationQuestion] = []
    answered_keys: List[str] = []
    if bool(args.developer_clarifications):
        role_questions = service.build_clarification_questions(
            snapshot,
            artifacts,
            metadata_hints=combined_hints,
            phase="roles",
        )
        if role_questions:
            print("Developer clarification pass 1/2: table roles")
            role_answers = _collect_answers(role_questions)
            role_hints = service.clarification_hints_from_answers(role_questions, role_answers)
            if role_hints:
                developer_clarifications = service.merge_metadata_hints(developer_clarifications, role_hints)
                combined_hints = service.merge_metadata_hints(metadata_hints, developer_clarifications)
                artifacts = service.build_artifacts(
                    str(args.domain or "").strip(),
                    snapshot,
                    description=str(args.description or "").strip(),
                    metadata_hints=combined_hints,
                )
                answered_keys.extend(sorted(role_answers.keys()))
            asked_questions.extend(role_questions)

        detail_questions = service.build_clarification_questions(
            snapshot,
            artifacts,
            metadata_hints=combined_hints,
            phase="details",
        )
        if simple_mode:
            detail_questions = [
                question
                for question in detail_questions
                if question.key.startswith("entities.")
            ]
        if detail_questions:
            print()
            if simple_mode:
                print("Developer clarification pass 2/2: table meaning and labels")
            else:
                print("Developer clarification pass 2/2: semantics and important columns")
            detail_answers = _collect_answers(detail_questions)
            detail_hints = service.clarification_hints_from_answers(detail_questions, detail_answers)
            if detail_hints:
                developer_clarifications = service.merge_metadata_hints(developer_clarifications, detail_hints)
                combined_hints = service.merge_metadata_hints(metadata_hints, developer_clarifications)
                artifacts = service.build_artifacts(
                    str(args.domain or "").strip(),
                    snapshot,
                    description=str(args.description or "").strip(),
                    metadata_hints=combined_hints,
                )
                answered_keys.extend(sorted(detail_answers.keys()))
            asked_questions.extend(detail_questions)

    if developer_clarifications:
        artifacts.root_json_files["developer_clarifications.json"] = developer_clarifications
        artifacts.review_report["developer_clarifications"] = {
            "question_count": len(asked_questions),
            "answered_keys": sorted(set(answered_keys)),
            "source_file": persisted_clarifications_path.as_posix(),
        }
        artifacts.root_json_files["review_report.json"] = artifacts.review_report

    artifacts = service.write_artifacts(
        artifacts,
        output_root=output_root,
        force=bool(args.force),
    )
    needs_review = artifacts.review_report.get("needs_review") or []
    print(f"Generated domain `{artifacts.domain_name}`")
    print(f"Files written: {len(artifacts.written_files)}")
    print(f"Needs review: {len(needs_review)}")
    print(f"Review report: {(Path(args.output_root) / artifacts.domain_name / 'review_report.json').as_posix()}")
    if developer_clarifications:
        print(f"Developer clarifications: {(Path(args.output_root) / artifacts.domain_name / 'developer_clarifications.json').as_posix()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
