#!/usr/bin/env python3
"""Benchmark the configured OpenAI-compatible LLM endpoint."""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib import error, request

ROOT_DIR = Path(__file__).resolve().parents[1]
DEFAULT_ENV_FILE = ROOT_DIR / ".env"


@dataclass
class HttpResult:
    ok: bool
    status: int | None
    total_s: float
    ttft_s: float | None
    headers: dict[str, str]
    body: dict[str, Any] | list[Any] | None
    raw_body: str
    error_message: str | None = None


@dataclass
class SampleResult:
    case: str
    sample: int
    ok: bool
    status: int | None
    total_s: float
    ttft_s: float | None
    prompt_tokens: int | None
    completion_tokens: int | None
    total_tokens: int | None
    tokens_per_s: float | None
    model_echoed: str | None
    preview: str | None
    error_message: str | None = None


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Discover the configured LLM endpoint, validate the configured model "
            "against /models, and run repeated latency checks with app-like prompts."
        )
    )
    parser.add_argument("--env-file", default=str(DEFAULT_ENV_FILE), help="Path to the .env file to read.")
    parser.add_argument("--base-url", help="Override LLM base URL.")
    parser.add_argument("--model", help="Override model id for completion requests.")
    parser.add_argument("--api-key", help="Override API key.")
    parser.add_argument("--samples", type=int, default=3, help="Number of samples to run per case.")
    parser.add_argument("--timeout", type=float, default=30.0, help="Per-request timeout in seconds.")
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON summary.")
    return parser.parse_args()


def load_config(args: argparse.Namespace) -> dict[str, str]:
    env_values = read_env_file(Path(args.env_file))
    base_url = (args.base_url or env_values.get("LLM_BASE_URL") or "").strip().rstrip("/")
    model = (args.model or env_values.get("LLM_MODEL") or "").strip()
    api_key = (args.api_key or env_values.get("LLM_API_KEY") or "").strip()
    if not base_url:
        raise SystemExit("Missing LLM_BASE_URL. Pass --base-url or set it in the env file.")
    if not model:
        raise SystemExit("Missing LLM_MODEL. Pass --model or set it in the env file.")
    return {"base_url": base_url, "model": model, "api_key": api_key}


def read_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = strip_optional_quotes(value.strip())
    return values


def strip_optional_quotes(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value


def build_cases(model: str) -> list[tuple[str, dict[str, Any]]]:
    return [
        (
            "tiny_ack",
            {
                "model": model,
                "messages": [{"role": "user", "content": "Reply with exactly OK"}],
                "temperature": 0,
            },
        ),
        (
            "intent_like",
            {
                "model": model,
                "messages": [
                    {
                        "role": "system",
                        "content": (
                            "Classify the maintenance query into exactly one label: "
                            "LIST, COUNT, SUMMARY, DETAIL, or ACTION. Return only the label."
                        ),
                    },
                    {
                        "role": "user",
                        "content": "Show my open maintenance work orders for today.",
                    },
                ],
                "temperature": 0,
            },
        ),
        (
            "sql_like",
            {
                "model": model,
                "messages": [
                    {
                        "role": "system",
                        "content": (
                            "You generate safe read-only SQL for a maintenance platform. "
                            "Return only one SELECT query. "
                            "Schema summary: "
                            "work_orders(id, title, status, priority, assignee_name, due_date, created_at, facility_name); "
                            "assets(id, asset_name, facility_name, criticality); "
                            "schedules(id, asset_id, schedule_name, next_due_date, owner_name, status). "
                            "Rules: never use INSERT, UPDATE, DELETE, DROP, ALTER, or TRUNCATE. "
                            "Use explicit column names and LIMIT 50 unless the user asks otherwise."
                        ),
                    },
                    {
                        "role": "user",
                        "content": (
                            "List overdue work orders for the Chennai facility assigned to Ravi. "
                            "Include title, priority, due date, and status."
                        ),
                    },
                ],
                "temperature": 0,
            },
        ),
        (
            "chat_like",
            {
                "model": model,
                "messages": [
                    {
                        "role": "system",
                        "content": "You are a concise enterprise assistant. Answer in 3 short bullet points.",
                    },
                    {
                        "role": "user",
                        "content": (
                            "Explain why preventive maintenance schedules matter for reducing downtime "
                            "in a manufacturing plant."
                        ),
                    },
                ],
                "temperature": 0.2,
            },
        ),
    ]


def http_json(
    method: str,
    url: str,
    timeout_s: float,
    api_key: str,
    payload: dict[str, Any] | None = None,
) -> HttpResult:
    headers = {"Accept": "application/json"}
    body_bytes: bytes | None = None
    if payload is not None:
        headers["Content-Type"] = "application/json"
        body_bytes = json.dumps(payload).encode("utf-8")
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    req = request.Request(url, data=body_bytes, headers=headers, method=method)
    started = time.perf_counter()
    try:
        with request.urlopen(req, timeout=timeout_s) as response:
            ttft_s = time.perf_counter() - started
            raw_body = response.read().decode("utf-8", errors="replace")
            total_s = time.perf_counter() - started
            parsed_body = try_parse_json(raw_body)
            return HttpResult(
                ok=True,
                status=response.status,
                total_s=total_s,
                ttft_s=ttft_s,
                headers={key.lower(): value for key, value in response.getheaders()},
                body=parsed_body,
                raw_body=raw_body,
            )
    except error.HTTPError as exc:
        raw_body = exc.read().decode("utf-8", errors="replace")
        total_s = time.perf_counter() - started
        return HttpResult(
            ok=False,
            status=exc.code,
            total_s=total_s,
            ttft_s=None,
            headers={key.lower(): value for key, value in exc.headers.items()},
            body=try_parse_json(raw_body),
            raw_body=raw_body,
            error_message=f"HTTP {exc.code}",
        )
    except error.URLError as exc:
        total_s = time.perf_counter() - started
        return HttpResult(
            ok=False,
            status=None,
            total_s=total_s,
            ttft_s=None,
            headers={},
            body=None,
            raw_body="",
            error_message=str(exc.reason),
        )
    except TimeoutError:
        total_s = time.perf_counter() - started
        return HttpResult(
            ok=False,
            status=None,
            total_s=total_s,
            ttft_s=None,
            headers={},
            body=None,
            raw_body="",
            error_message="request timed out",
        )


def try_parse_json(raw_body: str) -> dict[str, Any] | list[Any] | None:
    if not raw_body.strip():
        return None
    try:
        parsed = json.loads(raw_body)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, (dict, list)) else None


def run_samples(
    base_url: str,
    model: str,
    api_key: str,
    samples: int,
    timeout_s: float,
) -> tuple[HttpResult, list[SampleResult]]:
    models_result = http_json("GET", f"{base_url}/models", timeout_s=timeout_s, api_key=api_key)
    sample_results: list[SampleResult] = []

    for case_name, payload in build_cases(model):
        for sample in range(1, samples + 1):
            result = http_json(
                "POST",
                f"{base_url}/chat/completions",
                timeout_s=timeout_s,
                api_key=api_key,
                payload=payload,
            )
            sample_results.append(to_sample_result(case_name, sample, result))

    return models_result, sample_results


def to_sample_result(case_name: str, sample: int, result: HttpResult) -> SampleResult:
    if not result.ok or not isinstance(result.body, dict):
        return SampleResult(
            case=case_name,
            sample=sample,
            ok=False,
            status=result.status,
            total_s=result.total_s,
            ttft_s=result.ttft_s,
            prompt_tokens=None,
            completion_tokens=None,
            total_tokens=None,
            tokens_per_s=None,
            model_echoed=None,
            preview=None,
            error_message=result.error_message or "invalid response",
        )

    usage = result.body.get("usage") if isinstance(result.body.get("usage"), dict) else {}
    choices = result.body.get("choices") if isinstance(result.body.get("choices"), list) else []
    preview = ""
    if choices and isinstance(choices[0], dict):
        message = choices[0].get("message")
        if isinstance(message, dict):
            preview = str(message.get("content", "")).replace("\n", " ").strip()

    completion_tokens = as_int(usage.get("completion_tokens"))
    total_s = result.total_s
    tokens_per_s = None
    if completion_tokens is not None and total_s > 0:
        tokens_per_s = round(completion_tokens / total_s, 2)

    return SampleResult(
        case=case_name,
        sample=sample,
        ok=True,
        status=result.status,
        total_s=total_s,
        ttft_s=result.ttft_s,
        prompt_tokens=as_int(usage.get("prompt_tokens")),
        completion_tokens=completion_tokens,
        total_tokens=as_int(usage.get("total_tokens")),
        tokens_per_s=tokens_per_s,
        model_echoed=str(result.body.get("model", "")) or None,
        preview=preview[:120] or None,
    )


def as_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def summarize_cases(sample_results: list[SampleResult]) -> dict[str, dict[str, Any]]:
    summary: dict[str, dict[str, Any]] = {}
    case_names = sorted({row.case for row in sample_results})
    for case_name in case_names:
        rows = [row for row in sample_results if row.case == case_name]
        successes = [row for row in rows if row.ok]
        failures = [row for row in rows if not row.ok]

        case_summary: dict[str, Any] = {
            "runs": len(rows),
            "ok_runs": len(successes),
            "failed_runs": len(failures),
        }
        if successes:
            totals = [row.total_s for row in successes]
            ttfts = [row.ttft_s for row in successes if row.ttft_s is not None]
            tok_s = [row.tokens_per_s for row in successes if row.tokens_per_s is not None]
            case_summary.update(
                {
                    "avg_total_s": round(statistics.mean(totals), 3),
                    "min_total_s": round(min(totals), 3),
                    "max_total_s": round(max(totals), 3),
                    "avg_ttft_s": round(statistics.mean(ttfts), 3) if ttfts else None,
                    "avg_completion_tok_per_s": round(statistics.mean(tok_s), 2) if tok_s else None,
                    "prompt_tokens": successes[0].prompt_tokens,
                    "completion_tokens_avg": round(
                        statistics.mean(
                            [row.completion_tokens for row in successes if row.completion_tokens is not None]
                        ),
                        1,
                    )
                    if any(row.completion_tokens is not None for row in successes)
                    else None,
                    "sample_preview": successes[0].preview,
                }
            )
        if failures:
            case_summary["errors"] = [failure.error_message for failure in failures]
        summary[case_name] = case_summary
    return summary


def estimate_tag_latency(case_summary: dict[str, dict[str, Any]]) -> dict[str, float]:
    estimates: dict[str, float] = {}
    chat_like = case_summary.get("chat_like", {})
    intent_like = case_summary.get("intent_like", {})
    sql_like = case_summary.get("sql_like", {})

    chat_avg = chat_like.get("avg_total_s")
    intent_avg = intent_like.get("avg_total_s")
    sql_avg = sql_like.get("avg_total_s")
    if isinstance(chat_avg, (int, float)):
        estimates["chat_llm_only_floor_s"] = round(float(chat_avg), 3)
    if isinstance(intent_avg, (int, float)) and isinstance(sql_avg, (int, float)):
        estimates["sql_2_stage_llm_floor_s"] = round(float(intent_avg) + float(sql_avg), 3)
        estimates["sql_3_stage_llm_floor_s"] = round(float(intent_avg) + (2 * float(sql_avg)), 3)
    return estimates


def advertised_models(models_result: HttpResult) -> list[str]:
    if not models_result.ok or not isinstance(models_result.body, dict):
        return []
    data = models_result.body.get("data")
    if not isinstance(data, list):
        return []
    model_ids: list[str] = []
    for item in data:
        if isinstance(item, dict) and item.get("id"):
            model_ids.append(str(item["id"]))
    return model_ids


def print_human_report(
    base_url: str,
    model: str,
    models_result: HttpResult,
    sample_results: list[SampleResult],
    case_summary: dict[str, dict[str, Any]],
) -> None:
    print(f"Endpoint: {base_url}")
    print(f"Configured model: {model}")
    model_ids = advertised_models(models_result)

    if models_result.ok:
        print(f"/models status: {models_result.status} in {models_result.total_s:.3f}s")
        if model_ids:
            print("Advertised models:")
            for model_id in model_ids:
                print(f"  - {model_id}")
        else:
            print("Advertised models: none returned")
    else:
        print(f"/models check failed: {models_result.error_message or 'request failed'}")

    if model_ids and model not in model_ids:
        print("Warning: configured model is not advertised by /models. This server may be aliasing or ignoring model ids.")

    for case_name in sorted(case_summary):
        summary = case_summary[case_name]
        print()
        print(f"[{case_name}]")
        print(
            "  runs={runs} ok={ok_runs} failed={failed_runs}".format(
                runs=summary["runs"],
                ok_runs=summary["ok_runs"],
                failed_runs=summary["failed_runs"],
            )
        )
        if summary.get("avg_total_s") is not None:
            print(
                "  avg_total={avg_total_s:.3f}s min={min_total_s:.3f}s max={max_total_s:.3f}s avg_ttft={avg_ttft_s:.3f}s".format(
                    avg_total_s=summary["avg_total_s"],
                    min_total_s=summary["min_total_s"],
                    max_total_s=summary["max_total_s"],
                    avg_ttft_s=summary["avg_ttft_s"] or 0.0,
                )
            )
            print(
                "  prompt_tokens={prompt_tokens} completion_tokens_avg={completion_tokens_avg} completion_tok_per_s={avg_completion_tok_per_s}".format(
                    prompt_tokens=summary.get("prompt_tokens"),
                    completion_tokens_avg=summary.get("completion_tokens_avg"),
                    avg_completion_tok_per_s=summary.get("avg_completion_tok_per_s"),
                )
            )
            if summary.get("sample_preview"):
                print(f"  sample_preview={summary['sample_preview']}")
        if summary.get("errors"):
            print("  errors:")
            for err in summary["errors"]:
                print(f"    - {err}")

    estimates = estimate_tag_latency(case_summary)
    if estimates:
        print()
        print("TAG LLM-only floor estimate:")
        if "chat_llm_only_floor_s" in estimates:
            print(f"  chat path ~= {estimates['chat_llm_only_floor_s']:.3f}s")
        if "sql_2_stage_llm_floor_s" in estimates:
            print(f"  sql path (2-stage) ~= {estimates['sql_2_stage_llm_floor_s']:.3f}s")
        if "sql_3_stage_llm_floor_s" in estimates:
            print(f"  sql path (3-stage) ~= {estimates['sql_3_stage_llm_floor_s']:.3f}s")
        print("  These are inference-based LLM-only timings and exclude TAG API, DB, cache, and network overhead.")


def build_json_report(
    base_url: str,
    model: str,
    models_result: HttpResult,
    sample_results: list[SampleResult],
    case_summary: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    return {
        "endpoint": base_url,
        "configured_model": model,
        "models_check": {
            "ok": models_result.ok,
            "status": models_result.status,
            "total_s": round(models_result.total_s, 6),
            "error_message": models_result.error_message,
            "advertised_models": advertised_models(models_result),
        },
        "warning_model_not_advertised": bool(
            advertised_models(models_result) and model not in advertised_models(models_result)
        ),
        "summary": case_summary,
        "samples": [
            {
                "case": row.case,
                "sample": row.sample,
                "ok": row.ok,
                "status": row.status,
                "total_s": round(row.total_s, 6),
                "ttft_s": round(row.ttft_s, 6) if row.ttft_s is not None else None,
                "prompt_tokens": row.prompt_tokens,
                "completion_tokens": row.completion_tokens,
                "total_tokens": row.total_tokens,
                "tokens_per_s": row.tokens_per_s,
                "model_echoed": row.model_echoed,
                "preview": row.preview,
                "error_message": row.error_message,
            }
            for row in sample_results
        ],
        "tag_llm_only_floor_estimate": estimate_tag_latency(case_summary),
    }


def main() -> int:
    args = parse_args()
    config = load_config(args)
    models_result, sample_results = run_samples(
        base_url=config["base_url"],
        model=config["model"],
        api_key=config["api_key"],
        samples=args.samples,
        timeout_s=args.timeout,
    )
    case_summary = summarize_cases(sample_results)

    if args.json:
        print(
            json.dumps(
                build_json_report(
                    base_url=config["base_url"],
                    model=config["model"],
                    models_result=models_result,
                    sample_results=sample_results,
                    case_summary=case_summary,
                ),
                indent=2,
            )
        )
    else:
        print_human_report(
            base_url=config["base_url"],
            model=config["model"],
            models_result=models_result,
            sample_results=sample_results,
            case_summary=case_summary,
        )

    successful_cases = sum(1 for summary in case_summary.values() if summary.get("ok_runs"))
    if successful_cases == len(case_summary):
        return 0
    if successful_cases > 0 or models_result.ok:
        return 1
    return 2


if __name__ == "__main__":
    sys.exit(main())
