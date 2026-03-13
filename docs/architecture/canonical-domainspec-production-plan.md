# Canonical DomainSpec And Production Plan

Date: 2026-03-13
Owner: Backend Platform

## Goal
Define the smallest canonical `DomainSpec` that keeps runtime behavior generic, reviewable, and production-safe.

This document is the practical target for:

- domain onboarding
- runtime simplification
- production customization
- latency reduction without unsafe behavior changes

## Canonical Runtime Rule
The runtime must consume exactly one merged typed model:

`DomainSpec`

Everything domain-specific must be loaded into that model before request handling begins.

The runtime should not read raw YAML or JSON sections directly during request execution.

## Minimal Authoring Model
Start with only three artifacts per domain:

- `domain.generated.yaml`
- `domain.manual.yaml`
- `review.json`

At startup:

`generated + manual -> validated DomainSpec`

`review.json` is not runtime input. It is release and onboarding metadata.

## Minimal DomainSpec
This is the smallest high-leverage contract for the current backend.

```yaml
domain:
  id:
  name:
  version:
  default_locale:
  supported_locales:

schema:
  tables:
  columns:
  primary_keys:
  foreign_keys:
  tenant_scope:

semantics:
  entities:
  aliases:
  display_fields:
  field_roles:
  enum_maps:
  searchable_fields:
  relations:

capabilities:
  routes:
  actions:
  workflows:
  reports:

policies:
  role_permissions:
  protected_resources:
  mutation_rules:
  approval_rules:
  output_rules:

language:
  labels:
  synonyms:
  response_templates:

ux:
  clarification_prompts:
  empty_state_messages:
  disambiguation_rules:
```

## Section Intent
### `domain`
- identity and release metadata
- locale defaults

### `schema`
- structural DB truth only
- no business guessing

### `semantics`
- canonical entities and field meaning
- this is where business interpretation lives

### `capabilities`
- what the assistant is allowed to do
- query, mutate, report, workflow

### `policies`
- authorization and safety rules
- destructive action rules
- response and audit constraints

### `language`
- user-facing vocabulary and multilingual support

### `ux`
- clarification, abstain, and disambiguation behavior
- kept separate from policy so wording changes do not affect authorization

## What Does Not Belong In Runtime Code
- domain aliases
- domain-specific joins
- field meaning such as `status`, `assignee`, `scheduled_date`
- per-domain clarification prompts
- per-domain mutation permissions
- report definitions
- workflow slot definitions
- multilingual vocabulary

## What Must Stay In Runtime Code
- HTTP and stream contract
- session and cache orchestration
- typed `DomainSpec` validation
- SQL parser safety
- protected system table blocking
- verifier and validator pipeline
- generic action execution engines
- metrics, audit, replay tooling

## Production Customization Plan
Use the following plan for a production release.

### Release Profile
Adopt one explicit release profile instead of ad hoc env drift:

- `prod-fast-safe`
- `prod-balanced`
- `prod-observe`

For this backend, prefer `prod-fast-safe` unless report workloads require longer DB time budgets.

### Recommended Production Settings
For chat and SQL-heavy production traffic:

```env
APP_ENV=production
LOG_LEVEL=INFO

LLM_TIMEOUT=12
LLM_MAX_RETRIES=0
LLM_RETRY_ATTEMPTS=1
LLM_RETRY_BACKOFF_SECONDS=0.2
INTENT_DETECTION_TIMEOUT_SECONDS=1.5

QUERY_TIMEOUT_SECONDS=12

CACHE_ENABLED=true
CACHE_TTL_SECONDS=300
METRICS_ENABLED=true
ENABLE_AUDIT_LOGGING=true

MUTATION_REQUIRE_EXPLICIT_PERMISSION=true
MUTATION_ALLOWED_ROLES=admin,superadmin
```

### Why These Settings
- `LLM_MAX_RETRIES=0`: avoid provider-side retry amplification
- `LLM_RETRY_ATTEMPTS=1`: one application attempt only
- `INTENT_DETECTION_TIMEOUT_SECONDS=1.5`: fail fast to deterministic fallback on slow intent detection
- `QUERY_TIMEOUT_SECONDS=12`: keeps chat from hanging on long-running DB paths

### Production Customization Areas
Customize these before release:

1. Domain package
- review aliases
- review field roles
- review tenant scoping
- review allowed actions

2. Policy package
- confirm mutation permissions
- confirm protected tables
- confirm approval-required actions

3. Language package
- define operator-facing labels
- define clarification wording
- add multilingual synonyms only if truly needed

4. Runtime budgets
- choose fast fail budgets for chat
- choose longer budgets only for explicit reports

5. Replay suite
- capture top production prompts
- include ambiguity, no-data, mutation-denied, follow-up, and report cases

## Production Release Sequence
### Phase A: Freeze
- freeze current request corpus
- snapshot latency and LLM-call metrics
- confirm stream contract tests are green

### Phase B: Canonicalize
- move active domain config into merged `DomainSpec`
- remove direct runtime reads from raw config sections
- keep current behavior functionally stable

### Phase C: Tighten
- reduce LLM wait budgets
- keep deterministic-first path enabled
- fail fast on invalid config at startup

### Phase D: Validate
- run replay suite
- run unit suite around routing, SQL build, SQL validate, flows, and stream contract
- compare p95 latency and timeout rate to baseline

### Phase E: Roll Out
- deploy canary
- monitor timeout, abstain, clarify, mutation-denied, and error-rate metrics
- only widen rollout if p95 and error rate stay within target

## What To Delete Before Production
Delete only code proven unused by the active runtime.

Safe deletion criteria:

- no imports from active modules
- no graph wiring references
- no tests depending on it
- documented as archived or low-use

Already safe class of removal:

- archived legacy helper modules not used by the active graph path

Do not delete:

- reporting path, unless product explicitly disables reports
- current YAML flow engine
- SQL validation path
- guardrail modules

## Current Immediate Simplification Path
If you want the highest return with lowest release risk, do this first:

1. `DomainSpec` becomes the only runtime contract.
2. Keep authoring to `generated.yaml + manual.yaml + review.json`.
3. Externalize policy and semantics before externalizing wording.
4. Keep route/chat/session code generic.
5. Remove archived modules and hardcoded wait budgets.

## Exit Criteria
The system is in the target shape when:

- runtime behavior changes through `DomainSpec`, not code branches
- new domain onboarding does not require core runtime edits
- slow LLM branches fail fast to deterministic behavior
- production rollout is gated by replay and latency metrics
