"""Business rules for the maintenance domain."""
import re
from typing import Any, Dict, List


def _metadata_first(metadata: Dict[str, Any], keys: List[str]) -> str:
    for key in keys:
        value = str(dict(metadata or {}).get(str(key or "").strip(), "")).strip()
        if value:
            return value
    return ""


def _company_name(metadata: Dict[str, Any], formatter_cfg: Dict[str, Any] | None = None) -> str:
    cfg = dict(formatter_cfg or {})
    company_obj_key = str(cfg.get("company_object_key", "company")).strip() or "company"
    company_obj_name_key = str(cfg.get("company_object_name_key", "name")).strip() or "name"

    company_obj = metadata.get(company_obj_key)
    company_obj_name = ""
    if isinstance(company_obj, dict):
        company_obj_name = str(company_obj.get(company_obj_name_key) or "").strip()

    company_name_keys = [
        str(item).strip()
        for item in (cfg.get("company_name_keys") or ["company_name", "companyName"])
        if str(item).strip()
    ]
    for candidate in (_metadata_first(metadata, company_name_keys), company_obj_name):
        cleaned = str(candidate or "").strip()
        if cleaned:
            return cleaned
    return ""


def _self_display_name(metadata: Dict[str, Any], formatter_cfg: Dict[str, Any] | None = None) -> str:
    cfg = dict(formatter_cfg or {})
    display_name_keys = [
        str(item).strip()
        for item in (cfg.get("display_name_keys") or ["user_name"])
        if str(item).strip()
    ]
    assignee_name = _metadata_first(metadata, display_name_keys)
    if not assignee_name:
        return ""

    invalid_names = {
        str(item).strip().casefold()
        for item in (cfg.get("invalid_display_names") or ["user", "unknown", "na", "n/a", "null", "none"])
        if str(item).strip()
    }
    lowered = assignee_name.casefold()
    if lowered in invalid_names:
        return ""
    company_name = _company_name(metadata, cfg)
    if company_name and lowered == company_name.casefold():
        return ""
    first = assignee_name.split()[0].strip()
    return first if first else assignee_name


def _normalize_key(value: Any) -> str:
    return str(value or "").strip()


def _normalize_text(value: Any) -> str:
    return str(value or "").strip().lower()


def _flow_visibility_rules(config: Dict[str, Any], table: str) -> List[Dict[str, Any]]:
    section = (config or {}).get("flow_field_visibility_rules")
    if not isinstance(section, dict):
        return []
    rules: List[Dict[str, Any]] = []
    for key in (_normalize_key(table), "*"):
        payload = section.get(key)
        if isinstance(payload, list):
            rules.extend(item for item in payload if isinstance(item, dict))
    return rules


def _flow_candidate_config(config: Dict[str, Any], table: str) -> Dict[str, Any]:
    section = (config or {}).get("flow_candidate_rules")
    if not isinstance(section, dict):
        return {}
    payload = section.get(_normalize_key(table))
    if not isinstance(payload, dict):
        payload = section.get("*")
    return dict(payload) if isinstance(payload, dict) else {}


def _matches_rule_value(actual: Any, expected: Any) -> bool:
    actual_text = _normalize_text(actual)
    if isinstance(expected, list):
        return any(_matches_rule_value(actual, item) for item in expected)
    if isinstance(expected, dict):
        exists = expected.get("exists")
        if exists is not None:
            has_value = actual not in (None, "", " ")
            if bool(exists) != has_value:
                return False
        if "eq" in expected and not _matches_rule_value(actual, expected.get("eq")):
            return False
        if "equals" in expected and not _matches_rule_value(actual, expected.get("equals")):
            return False
        if "neq" in expected and _matches_rule_value(actual, expected.get("neq")):
            return False
        if "not_equals" in expected and _matches_rule_value(actual, expected.get("not_equals")):
            return False
        if "in" in expected:
            choices = expected.get("in")
            if not isinstance(choices, list) or not any(_matches_rule_value(actual, item) for item in choices):
                return False
        if "not_in" in expected:
            choices = expected.get("not_in")
            if isinstance(choices, list) and any(_matches_rule_value(actual, item) for item in choices):
                return False
        return True
    return actual_text == _normalize_text(expected)


def _matches_when_clause(when: Dict[str, Any], collected_fields: Dict[str, Any]) -> bool:
    for raw_key, expected in dict(when or {}).items():
        key = _normalize_key(raw_key)
        if not key:
            continue
        actual = dict(collected_fields or {}).get(key)
        if not _matches_rule_value(actual, expected):
            return False
    return True


def _normalized_patterns(raw_patterns: Any) -> List[str]:
    if isinstance(raw_patterns, str):
        pattern = raw_patterns.strip()
        return [pattern] if pattern else []
    if not isinstance(raw_patterns, list):
        return []
    cleaned: List[str] = []
    for item in raw_patterns:
        pattern = _normalize_key(item)
        if pattern:
            cleaned.append(pattern)
    return cleaned


def _matches_any_pattern(message: str, patterns: List[str]) -> bool:
    return any(re.search(pattern, message, flags=re.IGNORECASE) for pattern in patterns)


def _matches_all_patterns(message: str, patterns: List[str]) -> bool:
    return bool(patterns) and all(re.search(pattern, message, flags=re.IGNORECASE) for pattern in patterns)


def apply_conditional_fields(
    table: str,
    required_fields: List[str],
    collected_fields: Dict[str, Any],
    config: Dict[str, Any] | None = None,
) -> List[str]:
    visible = [str(item) for item in list(required_fields or []) if _normalize_key(item)]
    rules = _flow_visibility_rules(dict(config or {}), table)
    if not rules:
        return visible

    for rule in rules:
        when_clause = rule.get("when")
        if isinstance(when_clause, dict) and not _matches_when_clause(when_clause, dict(collected_fields or {})):
            continue
        excludes = {_normalize_key(item) for item in (rule.get("exclude") or []) if _normalize_key(item)}
        if excludes:
            visible = [field for field in visible if field not in excludes]
        includes = [_normalize_key(item) for item in (rule.get("include") or []) if _normalize_key(item)]
        for field in includes:
            if field not in visible:
                visible.append(field)
    return visible


def is_flow_candidate(message: str, table: str, config: Dict[str, Any] | None = None) -> bool:
    msg = str(message or "").strip().lower()
    if not msg:
        return False

    candidate_cfg = _flow_candidate_config(dict(config or {}), table)
    if not candidate_cfg:
        return False

    exclude_patterns = _normalized_patterns(candidate_cfg.get("exclude") or candidate_cfg.get("none_patterns"))
    if exclude_patterns and _matches_any_pattern(msg, exclude_patterns):
        return False

    match_any_patterns = _normalized_patterns(candidate_cfg.get("match_any") or candidate_cfg.get("any_patterns"))
    if match_any_patterns and _matches_any_pattern(msg, match_any_patterns):
        return True

    match_all_raw = candidate_cfg.get("match_all") or candidate_cfg.get("all_patterns")
    match_any_all_raw = candidate_cfg.get("match_any_all")

    if not match_any_all_raw and isinstance(match_all_raw, list) and any(isinstance(item, list) for item in match_all_raw):
        match_any_all_raw = match_all_raw
        match_all_raw = []

    match_all_patterns = _normalized_patterns(match_all_raw)
    if match_all_patterns and _matches_all_patterns(msg, match_all_patterns):
        return True

    if isinstance(match_any_all_raw, list):
        for group in match_any_all_raw:
            group_patterns = _normalized_patterns(group)
            if group_patterns and _matches_all_patterns(msg, group_patterns):
                return True

    default_value = _normalize_text(candidate_cfg.get("default"))
    return default_value in {"1", "true", "yes", "on"}


def _flow_slot_config(config: Dict[str, Any], table: str) -> Dict[str, Any]:
    section = (config or {}).get("flow_slot_resolution")
    if not isinstance(section, dict):
        return {}
    payload = section.get(str(table or "").strip())
    return dict(payload) if isinstance(payload, dict) else {}


def _message_hint_config(config: Dict[str, Any], table: str) -> Dict[str, Any]:
    slot_cfg = _flow_slot_config(config, table)
    payload = slot_cfg.get("message_hint")
    return dict(payload) if isinstance(payload, dict) else {}


def _clean_prefill_candidate(value: str, hint_cfg: Dict[str, Any] | None = None) -> str:
    candidate = str(value or "").strip()
    if not candidate:
        return ""

    cfg = dict(hint_cfg or {})
    stop_terms = [str(item).strip().lower() for item in (cfg.get("clause_stop_terms") or []) if str(item).strip()]
    if not stop_terms:
        stop_terms = ["today", "tomorrow", "yesterday", "on", "at", "with", "priority", "status", "due", "scheduled", "schedule"]
    stop_pattern = "|".join(re.escape(item) for item in stop_terms)
    candidate = re.split(
        rf"\b({stop_pattern})\b",
        candidate,
        maxsplit=1,
        flags=re.IGNORECASE,
    )[0].strip()

    leading_articles = [str(item).strip().lower() for item in (cfg.get("leading_article_terms") or []) if str(item).strip()]
    if not leading_articles:
        leading_articles = ["the", "a", "an"]
    article_pattern = "|".join(re.escape(item) for item in leading_articles)
    candidate = re.sub(rf"^({article_pattern})\s+", "", candidate, flags=re.IGNORECASE)

    leading_entities = [str(item).strip().lower() for item in (cfg.get("leading_entity_terms") or []) if str(item).strip()]
    if not leading_entities:
        leading_entities = ["facility", "asset", "user", "assignee"]
    entity_pattern = "|".join(re.escape(item) for item in leading_entities)
    candidate = re.sub(rf"^({entity_pattern})\s+", "", candidate, flags=re.IGNORECASE)

    candidate = re.sub(r"\s+", " ", candidate).strip(" ,.;:-")
    return candidate


def _extract_for_clause_candidates(message: str, hint_cfg: Dict[str, Any] | None = None) -> List[str]:
    text = str(message or "").strip()
    if not text:
        return []
    cfg = dict(hint_cfg or {})
    pattern = str(cfg.get("for_clause_pattern", "")).strip() or r"\bfor\s+(.+?)(?=\s+\bfor\b|$)"
    matches = re.findall(pattern, text, flags=re.IGNORECASE)
    cleaned: List[str] = []
    for raw in matches:
        raw_value = raw[0] if isinstance(raw, tuple) and raw else raw
        candidate = _clean_prefill_candidate(str(raw_value or ""), cfg)
        if candidate:
            cleaned.append(candidate)
    return cleaned


def _extract_to_clause_candidate(message: str, hint_cfg: Dict[str, Any] | None = None) -> str:
    text = str(message or "").strip()
    if not text:
        return ""
    cfg = dict(hint_cfg or {})
    pattern = (
        str(cfg.get("to_clause_pattern", "")).strip()
        or r"\bto\s+(.+?)(?=\s+\b(for|on|at|with|today|tomorrow|yesterday|priority|status)\b|$)"
    )
    match = re.search(pattern, text, flags=re.IGNORECASE)
    if not match:
        return ""
    return _clean_prefill_candidate(match.group(1), cfg)


def _looks_like_facility_name(candidate: str, slot_cfg: Dict[str, Any], hint_cfg: Dict[str, Any]) -> bool:
    text = str(candidate or "").strip().lower()
    if not text:
        return False
    if any(ch.isdigit() for ch in text) or any(ch in text for ch in {"_", "/"}):
        return True
    tokens = [tok for tok in re.split(r"[^a-z0-9]+", text) if tok]
    if not tokens:
        return False
    hint_tokens = {
        str(item).strip().lower()
        for item in (
            hint_cfg.get("facility_name_hint_tokens")
            or slot_cfg.get("facility_name_hint_tokens")
            or []
        )
        if str(item).strip()
    }
    if any(tok in hint_tokens for tok in tokens):
        return True
    return len(tokens) >= 3


def _extract_message_prefill_hints(message: str, table: str, config: Dict[str, Any]) -> Dict[str, str]:
    text = str(message or "").strip()
    if not text:
        return {}

    slot_cfg = _flow_slot_config(config, table)
    hint_cfg = _message_hint_config(config, table)
    if not slot_cfg or not hint_cfg:
        return {}

    hint_fields = [str(item).strip() for item in (slot_cfg.get("message_hint_fields") or []) if str(item).strip()]
    dual_for_fields = [str(item).strip() for item in (hint_cfg.get("dual_for_fields") or []) if str(item).strip()]

    default_user_field = next((field for field in hint_fields if "user" in field.lower() or "assignee" in field.lower()), "")
    default_facility_field = next(
        (field for field in hint_fields if "facility" in field.lower() or "location" in field.lower()),
        hint_fields[0] if hint_fields else "",
    )
    default_asset_field = next((field for field in hint_fields if "asset" in field.lower()), "")

    user_field = str(hint_cfg.get("user_field", default_user_field)).strip() or default_user_field
    facility_field = str(hint_cfg.get("facility_field", default_facility_field)).strip() or default_facility_field
    asset_field = str(hint_cfg.get("asset_field", default_asset_field)).strip() or default_asset_field
    to_clause_field = str(hint_cfg.get("to_clause_field", user_field)).strip() or user_field

    if not dual_for_fields:
        if facility_field and user_field:
            dual_for_fields = [facility_field, user_field]
        elif len(hint_fields) >= 2:
            dual_for_fields = [hint_fields[0], hint_fields[1]]
        elif hint_fields:
            dual_for_fields = [hint_fields[0]]

    lowered = text.lower()
    for_candidates = _extract_for_clause_candidates(text, hint_cfg)
    to_candidate = _extract_to_clause_candidate(text, hint_cfg)

    hints: Dict[str, str] = {}
    pair_patterns = hint_cfg.get("pair_patterns")
    if isinstance(pair_patterns, list):
        for raw_pattern in pair_patterns:
            pattern = str(raw_pattern or "").strip()
            if not pattern:
                continue
            match = re.search(pattern, text, flags=re.IGNORECASE)
            if not match:
                continue
            payload: Dict[str, str] = {}
            for field, raw_value in (match.groupdict() or {}).items():
                key = str(field or "").strip()
                candidate = _clean_prefill_candidate(raw_value, hint_cfg)
                if key and candidate:
                    payload[key] = candidate
            if payload:
                return payload

    if len(for_candidates) >= 2:
        if len(dual_for_fields) == 1:
            first = str(for_candidates[0]).strip()
            if first:
                hints[dual_for_fields[0]] = first
        elif len(dual_for_fields) >= 2:
            first = str(for_candidates[0]).strip()
            last = str(for_candidates[-1]).strip()
            if first:
                hints[dual_for_fields[0]] = first
            if last:
                hints[dual_for_fields[1]] = last
        return hints

    if len(for_candidates) == 1:
        candidate = str(for_candidates[0]).strip()
        if candidate:
            explicit_facility_terms = [
                str(item).strip().lower()
                for item in (hint_cfg.get("explicit_facility_terms") or [])
                if str(item).strip()
            ] or ["facility", "facilities"]
            explicit_asset_terms = [
                str(item).strip().lower()
                for item in (hint_cfg.get("explicit_asset_terms") or [])
                if str(item).strip()
            ] or ["asset", "assets"]
            assign_style_terms = [
                str(item).strip().lower()
                for item in (hint_cfg.get("assign_style_terms") or [])
                if str(item).strip()
            ] or ["assign", "assigned", "assignee"]

            explicit_facility = any(re.search(rf"\b{re.escape(term)}\b", lowered) for term in explicit_facility_terms)
            explicit_asset = any(re.search(rf"\b{re.escape(term)}\b", lowered) for term in explicit_asset_terms)
            assign_style = any(re.search(rf"\b{re.escape(term)}\b", lowered) for term in assign_style_terms)
            facility_like = _looks_like_facility_name(candidate, slot_cfg, hint_cfg)
            if (explicit_facility and not assign_style) or facility_like:
                target = facility_field or user_field
                if target:
                    hints[target] = candidate
            elif explicit_asset and not assign_style:
                target = asset_field or user_field
                if target:
                    hints[target] = candidate
            else:
                target = user_field or facility_field or asset_field
                if target:
                    hints[target] = candidate

    if to_candidate and to_clause_field and not str(hints.get(to_clause_field, "")).strip():
        hints[to_clause_field] = to_candidate
    return hints


def normalize_flow_fields(table: str, fields: Dict[str, Any], config: Dict[str, Any] | None = None) -> Dict[str, Any]:
    slot_cfg = _flow_slot_config(dict(config or {}), table)
    aliases = slot_cfg.get("field_aliases")
    alias_map: Dict[str, str] = {}
    if isinstance(aliases, dict):
        for raw_key, raw_value in aliases.items():
            key = str(raw_key or "").strip().lower()
            value = str(raw_value or "").strip()
            if key and value:
                alias_map[key] = value

    normalized: Dict[str, Any] = {}
    for raw_key, raw_value in dict(fields or {}).items():
        key = str(raw_key or "").strip()
        if not key:
            continue
        mapped_key = alias_map.get(key.lower(), key)
        value = raw_value.strip() if isinstance(raw_value, str) else raw_value
        existing = normalized.get(mapped_key)
        if existing not in (None, "", " ") and value in (None, "", " "):
            continue
        normalized[mapped_key] = value
    return normalized


def resolve_flow_slot_prefill(
    message: str,
    table: str,
    operation: str,
    initial_fields: Dict[str, Any],
    allow_message_fallback: bool = True,
    config: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    table_name = str(table or "").strip()
    op = str(operation or "").strip().lower()
    slot_cfg = _flow_slot_config(dict(config or {}), table_name)
    if not slot_cfg or op != "insert":
        return {"values": {}, "search": {}, "llm_slots_present": False}

    normalized_fields = normalize_flow_fields(table_name, initial_fields, config=dict(config or {}))
    lookup_fields = [str(item).strip() for item in (slot_cfg.get("lookup_fields") or []) if str(item).strip()]
    task_for_field = str(slot_cfg.get("task_for_field", "task_for")).strip() or "task_for"
    llm_slot_fields = [str(item).strip() for item in (slot_cfg.get("llm_slot_fields") or []) if str(item).strip()]
    if not llm_slot_fields:
        llm_slot_fields = list(lookup_fields)
        llm_slot_fields.append(task_for_field)
    llm_slots_present = any(str(normalized_fields.get(field, "")).strip() for field in llm_slot_fields)

    values: Dict[str, Any] = {}
    search: Dict[str, str] = {}

    task_for_value = str(normalized_fields.get(task_for_field, "")).strip().lower()
    infer_map = slot_cfg.get("task_for_inference")
    if not task_for_value and isinstance(infer_map, dict):
        for inferred_task_for, source_field in infer_map.items():
            src = str(source_field or "").strip()
            if src and str(normalized_fields.get(src, "")).strip():
                values[task_for_field] = str(inferred_task_for or "").strip().lower()
                break

    for field in lookup_fields:
        text_value = str(normalized_fields.get(field, "")).strip()
        if not text_value or text_value.isdigit():
            continue
        search[field] = text_value

    if not allow_message_fallback:
        return {"values": values, "search": search, "llm_slots_present": llm_slots_present}

    hints = _extract_message_prefill_hints(message, table_name, dict(config or {}))
    if not hints:
        return {"values": values, "search": search, "llm_slots_present": llm_slots_present}

    hint_fields = [str(item).strip() for item in (slot_cfg.get("message_hint_fields") or []) if str(item).strip()]
    if not hint_fields:
        hint_fields = list(lookup_fields)

    for field in hint_fields:
        if str(normalized_fields.get(field, "")).strip():
            continue
        candidate = str(hints.get(field, "")).strip()
        if candidate:
            search[field] = candidate

    if task_for_field not in values and not task_for_value and isinstance(infer_map, dict):
        for inferred_task_for, source_field in infer_map.items():
            src = str(source_field or "").strip()
            if src and str(hints.get(src, "")).strip():
                values[task_for_field] = str(inferred_task_for or "").strip().lower()
                break

    return {"values": values, "search": search, "llm_slots_present": llm_slots_present}


def format_no_records_message(context: Dict[str, Any]) -> str:
    """
    Domain-specific no-record wording hook.

    Args:
        context: {
            "sql": str,
            "metadata": dict,
            "response_messages": dict
        }
    """
    payload = dict(context or {})
    sql = str(payload.get("sql", "") or "")
    metadata = dict(payload.get("metadata") or {})
    response_messages = dict(payload.get("response_messages") or {})
    config = dict(payload.get("config") or {})

    formatter_cfg = config.get("no_records_formatter")
    if not isinstance(formatter_cfg, dict):
        return ""

    rules = formatter_cfg.get("rules")
    if not isinstance(rules, list):
        return ""

    lowered_sql = sql.lower()
    for rule in rules:
        if not isinstance(rule, dict):
            continue

        sql_contains = [str(item).strip().lower() for item in (rule.get("sql_contains") or []) if str(item).strip()]
        if sql_contains and not all(token in lowered_sql for token in sql_contains):
            continue

        captured_sql_value = ""
        capture_pattern = str(rule.get("sql_capture_pattern", "")).strip()
        if capture_pattern:
            sql_capture = re.search(capture_pattern, sql, flags=re.IGNORECASE)
            if not sql_capture:
                continue
            try:
                capture_group = int(rule.get("sql_capture_group", 1) or 1)
            except Exception:
                capture_group = 1
            try:
                captured_sql_value = str(sql_capture.group(capture_group) or "").strip()
            except Exception:
                captured_sql_value = ""
            if not captured_sql_value:
                continue

        metadata_user_keys = [str(item).strip() for item in (rule.get("metadata_user_keys") or []) if str(item).strip()]
        metadata_user_value = _metadata_first(metadata, metadata_user_keys)
        require_match = bool(rule.get("require_metadata_match", bool(metadata_user_keys)))
        if require_match and (
            not metadata_user_value
            or not captured_sql_value
            or metadata_user_value != captured_sql_value
        ):
            continue

        message_key = str(rule.get("response_message_key", "")).strip()
        template = str(response_messages.get(message_key, "")).strip() if message_key else ""
        fallback_message = str(rule.get("fallback_message", "")).strip()
        inject_display_name = bool(rule.get("inject_display_name", False))
        display_name = _self_display_name(metadata, formatter_cfg) if inject_display_name else ""

        if template:
            if "{name}" in template and display_name:
                return template.replace("{name}", display_name)
            if "{name}" not in template:
                return template

        if fallback_message:
            if "{name}" in fallback_message and display_name:
                return fallback_message.replace("{name}", display_name)
            if "{name}" not in fallback_message:
                if display_name and inject_display_name:
                    return f"{display_name}, {fallback_message[0].lower() + fallback_message[1:]}" if len(fallback_message) > 1 else f"{display_name}, {fallback_message}"
                return fallback_message

    # Return empty string so the shared ResponseNode fallback can still
    # include parsed filter details for non-specialized cases.
    return ""
