# TAG Assistant Code Writing Patterns

Date: 2026-03-11
Purpose: Explain how the current codebase is written so new features can follow the existing architecture instead of fighting it.

## Read This Before Adding Features
Use this document when implementing new features. It summarizes the current writing style and architectural patterns of the repo so feature work can align with the existing system.

## Core Architectural Pattern
The application is organized around a small number of runtime layers:

- API entrypoints in `app/api/`
- orchestration and graph wiring in `app/assistant/`
- runtime services in `app/services/`
- domain behavior in `domains/`
- product and architecture docs in `docs/`

The codebase is moving toward:

- deterministic-first behavior
- config-driven domain behavior
- generic runtime services
- explicit safety validation
- smaller, more reviewable feature changes

## Existing Code Shape
### API Layer
Keep endpoints thin.

Endpoints should:
- normalize request input
- decode headers/context
- call orchestration/services
- preserve the stream/result contract

Endpoints should not:
- contain business logic
- duplicate routing logic
- bypass service-layer validation

### Orchestration Layer
Graph topology lives in `app/assistant/orchestration/graph.py`.

Use this layer to:
- wire nodes together
- preserve route behavior
- control execution order

Do not place domain-specific behavior directly in graph wiring.

### Service Layer
`app/services/chat/service.py` is the main orchestration entrypoint today.

Services are where most runtime behavior currently lives:
- session/history handling
- idempotency and caching
- flow continuation
- pre-graph shortcuts
- runtime coordination

New features should prefer adding focused services instead of growing one large multi-purpose method.

### Domain Layer
Domain behavior should come from manifests and validated config under `domains/`.

Prefer:
- manifest-driven behavior
- config-backed aliases and semantics
- registry-loaded domain rules

Avoid:
- hardcoding domain semantics in core runtime code
- feature behavior that only works for one domain unless that is explicitly intended

## Existing Safety Pattern
Safety is not only a prompt concern in this repo. Safety is enforced in runtime code.

Current safety-critical patterns:
- SQL must pass `SQLValidatorService`
- protected tables and commands must stay blocked
- ambiguity should prefer clarification, not silent guessing
- stream/result contract should remain stable

New features must not weaken these guarantees.

## Existing Extension Pattern
When adding a feature, prefer this order:

1. update or add docs/specs
2. identify the affected runtime path
3. add or extend focused service/module logic
4. wire it into the existing graph or orchestration path
5. add regression tests for the changed behavior

## How Features Should Be Added
### Good Approach
- read the app context first
- map the feature to chat, SQL, report, or flow path
- identify impacted files before editing
- prefer small, composable services
- preserve existing contracts
- add tests where behavior changes

### Bad Approach
- adding feature logic directly to endpoints
- bypassing validators for speed
- loading the entire repo mentally for every small task
- solving domain problems with hardcoded branches in generic runtime code
- changing output shape without checking downstream contracts

## Common Impact Areas To Check
Any new feature can affect more than the file being edited. Always check:

- API request/response contract
- stream result envelope shape
- session state and cache keys
- SQL safety and authorization assumptions
- domain registry/manifest lookups
- YAML flow continuation behavior
- metrics, audits, and logs
- tests for neighboring behavior

## Code Review Expectations For New Features
A good feature change in this repo should answer:

- what runtime path is affected?
- what existing behavior could regress?
- what safety rules still apply?
- why is this approach better than alternatives?
- what tests prove the change is safe?

## Relationship To Guardrails
The codebase is moving toward an explicit:

1. intermediate layer
2. verifier
3. validator

That pattern should also guide feature delivery:
- intermediate: summarize the feature, impacted paths, and evidence
- verifier: check whether the proposed change is consistent with the current code and docs
- validator: block low-confidence or unsafe implementation plans before coding

## Working Rule
Do not start feature implementation from scratch each time. Start from the documented architecture and patterns here, then inspect only the relevant code for the active feature.
