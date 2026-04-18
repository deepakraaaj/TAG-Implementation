"""
Extract enum mappings for a domain by combining:
  1. Distinct values of enum-style columns from a live DB.
  2. `public enum` definitions parsed from a Java/Spring source tree.

Matches each DB column to its enum class via name + value-overlap scoring,
and writes a JSON block ready to drop into
`generate_domain.request.json -> clarification_hints.enum_values`.

Usage:
    python scripts/extract_enums.py \\
        --source-dir /path/to/<app>-api/src/main/java \\
        --request-file scripts/generate_domain.request.json \\
        --output-file scripts/extracted_enums.<domain>.json \\
        [--merge]                # patch the request file in place

Works on any Kritilabs Java backend that uses the
    public enum X { LABEL("Display", id), ... }
convention. Frappe/ERPNext (Python) needs a separate parser.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple
from urllib.parse import urlparse

try:
    from sqlalchemy import create_engine, text
except ImportError:
    sys.stderr.write("sqlalchemy is required. `pip install sqlalchemy pymysql`\n")
    raise


# --------------------------------------------------------------------------- #
# Java enum parser
# --------------------------------------------------------------------------- #

ENUM_HEADER_RE = re.compile(r"public\s+enum\s+(\w+)\s*\{", re.MULTILINE)

# Matches: IDENT("Label", 1)   IDENT("Label", "CODE", 2)   IDENT("Label", 'L')
ENUM_MEMBER_RE = re.compile(
    r"""
    ^\s*([A-Z][A-Z0-9_]*)            # IDENT
    \s*\(
        \s*"((?:[^"\\]|\\.)*)"       # "label"
        \s*,
        (?:\s*"((?:[^"\\]|\\.)*)"\s*,)?    # optional "code"
        \s*('?[^,)]+?'?)             # value (int or 'X')
        (?:\s*,[^)]*)?               # ignore trailing args
    \)
    """,
    re.VERBOSE | re.MULTILINE,
)


@dataclass
class EnumDef:
    class_name: str
    file_path: Path
    # members: list of (ident, label, optional_code, raw_value)
    members: List[Tuple[str, str, Optional[str], str]] = field(default_factory=list)

    @property
    def value_to_label(self) -> Dict[str, str]:
        """Union of int/char IDs AND string codes → label.
        Lets us match both `state_source=14` and `packet_type='IF'`."""
        out: Dict[str, str] = {}
        for _ident, label, code, raw_value in self.members:
            value = self._coerce_value(raw_value)
            if value is not None:
                out[value] = label
            if code:
                out[code] = label
        return out

    @property
    def value_set(self) -> set:
        return set(self.value_to_label.keys())

    @property
    def label_set(self) -> set:
        """Lowercased labels — for matching varchar columns that store the label directly
        (e.g. ignition_status='Off'/'On' against IgnitionStatus.OFF('Off',0))."""
        return {label.lower() for _ident, label, _code, _val in self.members if label}

    @staticmethod
    def _coerce_value(raw: str) -> Optional[str]:
        token = raw.strip()
        if not token:
            return None
        # Char like 'L'
        if token.startswith("'") and token.endswith("'"):
            inner = token[1:-1]
            return inner if inner else None
        # Int
        if re.fullmatch(r"-?\d+", token):
            return token
        return None


def parse_java_enum_file(path: Path) -> List[EnumDef]:
    try:
        text_body = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []

    defs: List[EnumDef] = []
    for header in ENUM_HEADER_RE.finditer(text_body):
        class_name = header.group(1)
        # Find body: from header.end() to the next semicolon-ending member block.
        body_start = header.end()
        # Heuristic: members live before the first ';' that follows the header.
        # We also stop at the last ')' of the constants list.
        body_end = text_body.find(";", body_start)
        if body_end == -1:
            body_end = len(text_body)
        members_block = text_body[body_start:body_end]
        members: List[Tuple[str, str, Optional[str], str]] = []
        for m in ENUM_MEMBER_RE.finditer(members_block):
            ident, label, code, value = m.group(1), m.group(2), m.group(3), m.group(4)
            members.append((ident, label, code, value))
        if members:
            defs.append(EnumDef(class_name=class_name, file_path=path, members=members))
    return defs


def collect_java_enums(source_dir: Path) -> List[EnumDef]:
    out: List[EnumDef] = []
    for path in source_dir.rglob("*.java"):
        out.extend(parse_java_enum_file(path))
    return out


# --------------------------------------------------------------------------- #
# DB introspection
# --------------------------------------------------------------------------- #

ENUM_NAME_HINTS = ("type", "status", "category", "state", "source", "kind")

# Skip columns clearly not enum-like even if name matches.
NON_ENUM_TYPE_PREFIXES = ("DATE", "DATETIME", "TIMESTAMP", "JSON", "BLOB", "DECIMAL", "FLOAT", "DOUBLE", "TEXT", "LONGTEXT")

HEX_LIKE_RE = re.compile(r"^[0-9a-fA-F]{2,8}$")


def _is_dirty_or_bitmask(values: List[str]) -> bool:
    """Detect hex/bitmask/raw-byte columns that look enum-ish but aren't."""
    if not values:
        return True
    hex_count = sum(1 for v in values if HEX_LIKE_RE.fullmatch(v) and not v.isdigit())
    if hex_count >= max(2, len(values) // 3):
        return True
    # Mixed leading-zero strings ('0000', '0001') with non-zero shorts → bitfield
    has_padded = any(v.startswith("0") and len(v) > 1 and v.isdigit() for v in values)
    has_short = any(len(v) <= 2 and v.isdigit() for v in values)
    if has_padded and has_short:
        return True
    return False


def _is_fk_column(table: str, column: str) -> bool:
    """FK columns end in _id (but not the bare `id` PK)."""
    return column.endswith("_id") and column != "id"


_JDBC_ONLY_PARAMS = {"allowPublicKeyRetrieval", "useSSL", "serverTimezone", "characterEncoding"}

def _normalize_db_url(url: str) -> str:
    # Swap async drivers to sync pymysql.
    url = url.replace("+aiomysql", "+pymysql").replace("+asyncmy", "+pymysql")
    # Strip JDBC-only query params that PyMySQL rejects.
    if "?" in url:
        base, qs = url.split("?", 1)
        kept = [p for p in qs.split("&") if p.split("=")[0] not in _JDBC_ONLY_PARAMS]
        url = base + ("?" + "&".join(kept) if kept else "")
    return url


@dataclass
class ColumnSnapshot:
    table: str
    column: str
    column_type: str
    distinct_values: List[str]
    distinct_count: int
    null_count: int
    total_rows: int


def fetch_enum_candidate_columns(
    db_url: str,
    schema: str,
    include_tables: List[str],
    max_distinct: int = 32,
) -> List[ColumnSnapshot]:
    engine = create_engine(_normalize_db_url(db_url))
    snapshots: List[ColumnSnapshot] = []
    placeholders = ", ".join(f"'{t}'" for t in include_tables) or "''"
    name_filter = " OR ".join(f"COLUMN_NAME LIKE '%{h}%'" for h in ENUM_NAME_HINTS)

    cols_sql = text(
        f"""
        SELECT TABLE_NAME, COLUMN_NAME, COLUMN_TYPE
        FROM INFORMATION_SCHEMA.COLUMNS
        WHERE TABLE_SCHEMA = :schema
          AND TABLE_NAME IN ({placeholders})
          AND ({name_filter})
        ORDER BY TABLE_NAME, COLUMN_NAME
        """
    )

    with engine.connect() as conn:
        rows = conn.execute(cols_sql, {"schema": schema}).all()
        for table_name, column_name, column_type in rows:
            ctype = (column_type or "").upper()
            if any(ctype.startswith(p) for p in NON_ENUM_TYPE_PREFIXES):
                continue
            if _is_fk_column(table_name, column_name):
                continue
            # Master tables hold enum data, not enum-bearing columns.
            if table_name.endswith("_master") and "TEXT" in ctype.upper():
                continue
            # Pull distinct values + counts in one query.
            try:
                vals = conn.execute(
                    text(
                        f"SELECT `{column_name}` AS v, COUNT(*) AS c "
                        f"FROM `{table_name}` GROUP BY `{column_name}` "
                        f"ORDER BY c DESC LIMIT {max_distinct + 1}"
                    )
                ).all()
            except Exception:
                continue
            distinct_values: List[str] = []
            null_count = 0
            total = 0
            for v, c in vals:
                total += int(c)
                if v is None:
                    null_count += int(c)
                else:
                    distinct_values.append(str(v))
            if not distinct_values:
                continue
            if len(vals) > max_distinct:
                # Too many distinct → not an enum.
                continue
            if _is_dirty_or_bitmask(distinct_values):
                continue
            snapshots.append(
                ColumnSnapshot(
                    table=table_name,
                    column=column_name,
                    column_type=column_type,
                    distinct_values=distinct_values,
                    distinct_count=len(distinct_values),
                    null_count=null_count,
                    total_rows=total,
                )
            )
    return snapshots


# --------------------------------------------------------------------------- #
# Matching
# --------------------------------------------------------------------------- #

def _camel(words: Iterable[str]) -> str:
    return "".join(w[:1].upper() + w[1:].lower() for w in words if w)


def _tokens(name: str) -> List[str]:
    return [t for t in re.split(r"[_\s]+", name) if t]


def _candidate_class_names(table: str, column: str) -> List[str]:
    t_tokens = _tokens(table)
    c_tokens = _tokens(column)
    candidates = [
        _camel(t_tokens + c_tokens),
        _camel(c_tokens),
        _camel(t_tokens[:1] + c_tokens),
        _camel(c_tokens + t_tokens),
    ]
    seen, out = set(), []
    for c in candidates:
        if c and c not in seen:
            seen.add(c)
            out.append(c)
    return out


_GENERIC_TOKENS = {"status", "type", "category", "state", "id", "source", "kind", "vts", "transaction", "log", "master", "cfg", "config"}


def _name_score(class_name: str, candidates: List[str]) -> int:
    cn = class_name.lower()
    for i, cand in enumerate(candidates):
        if cn == cand.lower():
            return 100 - i * 5  # exact match, prefer earlier candidates
    cand_tokens_all = {t.lower() for c in candidates for t in re.findall(r"[A-Z][a-z]+|\d+", c)}
    name_tokens_all = {t.lower() for t in re.findall(r"[A-Z][a-z]+|\d+", class_name)}
    # Specific-token overlap (generic words excluded — those would let any *_status match any *Status enum).
    cand_specific = cand_tokens_all - _GENERIC_TOKENS
    name_specific = name_tokens_all - _GENERIC_TOKENS
    if not cand_specific or not name_specific:
        return 0
    overlap = len(cand_specific & name_specific)
    if overlap == 0:
        return 0
    return min(60, 20 + overlap * 15)


def _value_score(col_values: set, enum: "EnumDef") -> Tuple[int, int]:
    """Score by id/code overlap OR label overlap (whichever is higher)."""
    if not col_values:
        return 0, 0
    best_pct, best_n = 0, 0
    if enum.value_set:
        overlap = col_values & enum.value_set
        pct = round(100 * len(overlap) / len(col_values))
        if pct > best_pct:
            best_pct, best_n = pct, len(overlap)
    if enum.label_set:
        col_lower = {v.lower() for v in col_values}
        overlap = col_lower & enum.label_set
        pct = round(100 * len(overlap) / len(col_lower))
        if pct > best_pct:
            best_pct, best_n = pct, len(overlap)
    return best_pct, best_n


@dataclass
class Match:
    column_snapshot: ColumnSnapshot
    enum: EnumDef
    name_score: int
    value_score: int
    value_overlap: int
    total_score: int


def match_columns_to_enums(
    snapshots: List[ColumnSnapshot],
    enums: List[EnumDef],
    min_value_score: int = 75,
    min_name_score: int = 50,
    min_total_score: int = 130,
) -> Tuple[Dict[str, Match], List[Tuple[ColumnSnapshot, List[Match]]], List[ColumnSnapshot]]:
    """
    Returns:
      best_by_column: {f"{table}.{column}": Match}
      ambiguous: [(snapshot, top_candidates_list)]   needs_review
      unmatched: [snapshot]                          no candidate met threshold
    """
    best: Dict[str, Match] = {}
    ambiguous: List[Tuple[ColumnSnapshot, List[Match]]] = []
    unmatched: List[ColumnSnapshot] = []

    for snap in snapshots:
        col_value_set = set(snap.distinct_values)
        cands = _candidate_class_names(snap.table, snap.column)
        scored: List[Match] = []
        for ed in enums:
            ns = _name_score(ed.class_name, cands)
            vs, vo = _value_score(col_value_set, ed)
            if vs == 0:
                continue
            total = ns + vs
            scored.append(Match(snap, ed, ns, vs, vo, total))
        if not scored:
            unmatched.append(snap)
            continue
        scored.sort(key=lambda m: (m.total_score, m.value_score, m.name_score), reverse=True)
        top = scored[0]
        runner = scored[1] if len(scored) > 1 else None
        key = f"{snap.table}.{snap.column}"
        meets = (
            top.value_score >= min_value_score
            and top.name_score >= min_name_score
            and top.total_score >= min_total_score
            and (runner is None or top.total_score - runner.total_score >= 15)
        )
        if meets:
            best[key] = top
        else:
            ambiguous.append((snap, scored[:5]))
    return best, ambiguous, unmatched


# --------------------------------------------------------------------------- #
# Output
# --------------------------------------------------------------------------- #

def build_enum_values_block(
    matches: Dict[str, Match],
) -> Tuple[Dict[str, Dict[str, str]], Dict[str, Dict[str, Dict[str, str]]]]:
    """Returns (flat, nested):
      flat   : { column_name: { value: label, ... } }     # legacy TAG shape
      nested : { table: { column_name: { value: label, ... } } }   # collision-safe

    Emits the FULL enum mapping (not just DB-present values), so the bot
    understands every valid filter label even for values not seen yet.
    On flat-key collisions across tables, the table with the most rows wins;
    losers are recorded in the review.
    """
    nested: Dict[str, Dict[str, Dict[str, str]]] = {}
    for key, m in matches.items():
        table, col_name = key.split(".", 1)
        nested.setdefault(table, {})[col_name] = dict(m.enum.value_to_label)

    flat: Dict[str, Dict[str, str]] = {}
    by_col: Dict[str, List[Tuple[str, Match]]] = {}
    for key, m in matches.items():
        _, col_name = key.split(".", 1)
        by_col.setdefault(col_name, []).append((key, m))
    for col_name, items in by_col.items():
        items.sort(key=lambda kv: kv[1].column_snapshot.total_rows, reverse=True)
        flat[col_name] = dict(items[0][1].enum.value_to_label)
    return flat, nested


def build_review_report(
    matches: Dict[str, Match],
    ambiguous: List[Tuple[ColumnSnapshot, List[Match]]],
    unmatched: List[ColumnSnapshot],
) -> Dict[str, Any]:
    high_conf = []
    for key, m in sorted(matches.items()):
        high_conf.append({
            "column": key,
            "enum_class": m.enum.class_name,
            "enum_file": str(m.enum.file_path),
            "name_score": m.name_score,
            "value_score": m.value_score,
            "value_overlap": m.value_overlap,
            "distinct_db_values": m.column_snapshot.distinct_values,
        })
    needs_review = []
    for snap, cands in ambiguous:
        needs_review.append({
            "column": f"{snap.table}.{snap.column}",
            "distinct_db_values": snap.distinct_values,
            "candidates": [
                {
                    "enum_class": c.enum.class_name,
                    "enum_file": str(c.enum.file_path),
                    "name_score": c.name_score,
                    "value_score": c.value_score,
                    "value_overlap": c.value_overlap,
                    "mapping_preview": {v: c.enum.value_to_label.get(v, "?") for v in snap.distinct_values},
                }
                for c in cands
            ],
        })
    no_match = [
        {
            "column": f"{s.table}.{s.column}",
            "column_type": s.column_type,
            "distinct_db_values": s.distinct_values,
        }
        for s in unmatched
    ]

    # Detect cross-table column name collisions in the matched set.
    col_to_tables: Dict[str, List[str]] = {}
    for key in matches:
        table, col = key.split(".", 1)
        col_to_tables.setdefault(col, []).append(table)
    collisions = {col: tables for col, tables in col_to_tables.items() if len(tables) > 1}

    return {
        "summary": {
            "matched": len(matches),
            "needs_review": len(ambiguous),
            "no_match": len(unmatched),
        },
        "matched": high_conf,
        "needs_review": needs_review,
        "no_match": no_match,
        "column_name_collisions": collisions,
    }


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #

def schema_from_db_url(db_url: str) -> str:
    parsed = urlparse(db_url.replace("+aiomysql", "").replace("+asyncmy", "").replace("+pymysql", ""))
    return parsed.path.lstrip("/").split("?")[0]


def main() -> int:
    ap = argparse.ArgumentParser(description="Extract enum mappings from Java backend + live DB.")
    ap.add_argument("--source-dir", required=True, type=Path, help="Backend source root containing public enum classes.")
    ap.add_argument("--request-file", required=True, type=Path, help="Path to generate_domain.request.json.")
    ap.add_argument("--output-file", required=True, type=Path, help="Where to write extracted_enums JSON.")
    ap.add_argument("--merge", action="store_true", help="Patch request file's clarification_hints.enum_values in place.")
    ap.add_argument("--max-distinct", type=int, default=32, help="Skip columns with more distinct values than this.")
    ap.add_argument("--db-url", default=None, help="Override db_url from the request file (useful for local dev).")
    args = ap.parse_args()

    request = json.loads(args.request_file.read_text())
    req_block = request.get("request", {})
    db_url = args.db_url or req_block.get("db_url")
    include_tables = req_block.get("include_tables") or []
    if not db_url or not include_tables:
        sys.stderr.write("request file is missing db_url or include_tables\n")
        return 2

    schema = schema_from_db_url(db_url)
    print(f"[1/4] DB schema: {schema}", file=sys.stderr)
    print(f"[2/4] Scanning {len(include_tables)} tables for enum-style columns…", file=sys.stderr)
    snapshots = fetch_enum_candidate_columns(db_url, schema, include_tables, max_distinct=args.max_distinct)
    print(f"      → {len(snapshots)} candidate columns", file=sys.stderr)

    print(f"[3/4] Parsing Java enums under {args.source_dir}…", file=sys.stderr)
    enums = collect_java_enums(args.source_dir)
    print(f"      → {len(enums)} enum classes", file=sys.stderr)

    print("[4/4] Matching columns ↔ enums…", file=sys.stderr)
    matches, ambiguous, unmatched = match_columns_to_enums(snapshots, enums)
    enum_values_flat, enum_values_nested = build_enum_values_block(matches)
    review = build_review_report(matches, ambiguous, unmatched)

    output = {
        "_summary": review["summary"],
        "enum_values": enum_values_flat,
        "enum_values_by_table": enum_values_nested,
        "_review": review,
    }
    args.output_file.write_text(json.dumps(output, indent=2, sort_keys=False))
    print(f"      wrote {args.output_file}", file=sys.stderr)
    print(
        f"      matched={review['summary']['matched']}  "
        f"needs_review={review['summary']['needs_review']}  "
        f"no_match={review['summary']['no_match']}",
        file=sys.stderr,
    )

    if args.merge:
        ch = req_block.setdefault("clarification_hints", {})
        existing = ch.get("enum_values") or {}
        if not isinstance(existing, dict):
            existing = {}
        existing.update(enum_values_flat)
        ch["enum_values"] = existing
        args.request_file.write_text(json.dumps(request, indent=2))
        print(f"      merged enum_values into {args.request_file}", file=sys.stderr)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
