# Agent Spec: TAG Assistant Context

## Purpose
Use this file as the compact application context for future work in this repository.

## Read Order
1. `docs/product/tag-assistant/application-context.md`
2. `docs/product/tag-assistant/prd.md`
3. `README.md`
4. `docs/dev/tag-assistant/request-routing.md`
5. `docs/dev/tag-assistant/code-writing-patterns.md` for feature or bugfix requests

If the request touches LLM quality, token usage, verification, or hallucination control, also read:

6. `docs/dev/llm-guardrails/SPEC.md`
7. `docs/dev/llm-guardrails/DESIGN.md`

## Application Summary
- TAG is a FastAPI backend assistant for business operations.
- It supports chat, SQL-backed answers, reports, and guided flows.
- LangGraph orchestrates the main runtime graph.
- Redis stores session state and idempotency data.
- Domain behavior comes from manifests and registry-loaded config.
- SQL safety is enforced before execution.

## Main Goal
Convert natural-language requests into trustworthy business outcomes with deterministic-first handling, explicit uncertainty, and lower token usage.

## Default Assumptions
- `app/services/chat/service.py` is the main runtime orchestrator.
- `app/assistant/orchestration/graph.py` is the active graph wiring.
- `app/services/data/sql_validator.py` is authoritative for SQL safety.
- Product direction lives in `docs/product/tag-assistant/prd.md`.

## Working Rule
Do not re-explore the entire repository before every task. Start from the context docs above, then inspect only the files needed for the active request.

For feature requests, also use `docs/dev/tag-assistant/feature-change-workflow.md` and `.agents/SPECS/feature-change-evaluation.md`.
For bugfix requests, also use `docs/dev/tag-assistant/bugfix-workflow.md` and `.agents/SPECS/bugfix-evaluation.md`.
