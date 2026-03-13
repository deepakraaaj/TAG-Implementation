#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.domains.generator import DomainGenerationService


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
        help="Database URL to inspect. Defaults to the configured DATABASE_URL.",
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
        "--force",
        action="store_true",
        help="Overwrite known generated files if the domain folder already exists.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    service = DomainGenerationService()
    metadata_hints = {}
    metadata_file = Path(str(args.metadata_file or "").strip()) if str(args.metadata_file or "").strip() else None
    if metadata_file is not None:
        with metadata_file.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
        metadata_hints = dict(payload) if isinstance(payload, dict) else {}
    artifacts = service.generate_domain(
        domain_name=args.domain,
        db_url=str(args.db_url or "").strip() or None,
        output_root=Path(args.output_root),
        description=str(args.description or "").strip(),
        metadata_hints=metadata_hints,
        force=bool(args.force),
    )
    needs_review = artifacts.review_report.get("needs_review") or []
    print(f"Generated domain `{artifacts.domain_name}`")
    print(f"Files written: {len(artifacts.written_files)}")
    print(f"Needs review: {len(needs_review)}")
    print(f"Review report: {(Path(args.output_root) / artifacts.domain_name / 'review_report.json').as_posix()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
