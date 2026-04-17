# TAG Assistant Application Context

Date: 2026-03-11
Purpose: Canonical fast-start context for future requests in this repository.

## Read This First
If you are handling a new request in this repo, read this file first before exploring the codebase. It is the shortest path to understanding the application without rediscovering everything from scratch.

Then read:

1. `docs/product/tag-assistant/prd.md`
2. `README.md`
3. `docs/dev/tag-assistant/request-routing.md`
4. `docs/architecture/domain-runtime-overview.md`
5. `docs/dev/tag-assistant/code-writing-patterns.md` for feature or bugfix requests
6. `docs/dev/llm-guardrails/SPEC.md` if the task touches LLM quality, token use, safety, verification, or validation

## What This Application Is
TAG is a backend assistant runtime for business operations workflows. It exposes chat-style endpoints that turn user messages into:

- conversational answers
- SQL-backed results
- reports
- guided workflow actions

The runtime is designed to be domain-aware and increasingly domain-config-driven rather than domain-hardcoded.

## Current Runtime Stack
- API framework: FastAPI
- orchestration: LangGraph
- cache/session state: Redis
- database access: synchronous SQLAlchemy-based schema and execution services
- domain source of truth: `DomainRegistry` plus domain manifests under `domains/`
- observability: health endpoints, readiness, Prometheus metrics, audit support

## Active Request Paths
### Chat Path
Used for conversational replies and non-SQL interactions.

### SQL Path
Used for list/count/filter/update style operations:
- route
- intent
- SQL build
- SQL validate
- SQL execute
- respond

### Report Path
Used for report-oriented requests routed to reporting services.

## Current Product Goal
The application goal is to convert natural-language requests into trustworthy business outcomes while staying safe, explicit, and operationally maintainable.

That means:
- answer from validated data
- guide users through structured actions
- avoid guessing when evidence is weak
- reduce token waste with smaller, more structured runtime contracts

## What Is Already Strong
- stable API entrypoints and stream contract
- session and idempotency handling in `ChatService`
- SQL safety checks in `SQLValidatorService`
- domain registry and manifest-based behavior
- health and metrics foundations

## Main Current Gaps
- unsupported claims can still slip into final wording
- ambiguity handling is not strict enough yet
- evidence for final claims is not always explicit
- token usage is still larger than necessary on some paths
- too much orchestration logic is concentrated in a few runtime services

## Current Improvement Direction
The current strategic direction is to add an explicit guardrail pipeline:

1. intermediate frame
2. verifier
3. validator

This is intended to:
- reduce hallucination in practice
- make unsupported answers fail closed
- improve clarification behavior
- lower LLM token usage

## Important Files
- `README.md`: runtime snapshot and active module map
- `app/services/chat/service.py`: central orchestration entrypoint
- `app/assistant/orchestration/graph.py`: graph wiring
- `app/services/data/sql_validator.py`: SQL safety authority
- `docs/product/tag-assistant/prd.md`: application-level product direction
- `docs/dev/llm-guardrails/SPEC.md`: guardrail module contract
- `docs/dev/llm-guardrails/DESIGN.md`: guardrail implementation design

## Working Rule For Future Requests
Do not start by rediscovering the entire repo. Start from this document and the linked docs above. Explore code only after the current request is mapped to the relevant runtime path.

For feature requests, use the feature workflow in `docs/dev/tag-assistant/feature-change-workflow.md` before implementing code.
For bugfix requests, use `docs/dev/tag-assistant/bugfix-workflow.md` before patching.
