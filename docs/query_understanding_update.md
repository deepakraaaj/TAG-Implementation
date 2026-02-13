# Query Understanding Refactor and Contextualization Stabilization

## 1. Purpose
This document describes the recent refactor to improve multi-turn reliability in TAG backend chat-to-SQL flow, especially around entity drift (for example, `list users` followed by `list assets` incorrectly staying in user context).

The implementation introduces a shared query-understanding module and integrates it into contextualization and SQL generation. It also adds deterministic SQL fast-paths for common user and asset list/count requests.

## 2. Problem Statement
Observed issues before refactor:

1. Follow-up prompts such as `list assets` were occasionally contaminated by prior turn entity context (for example, users).
2. Rewriter and SQL generation were not using a single canonical understanding source.
3. Some straightforward list/count requests unnecessarily depended on LLM SQL generation, increasing inconsistency risk.
4. Mutation verbs like `update` were not consistently captured in fallback intent logic.

## 3. High-Level Solution
The solution is a hybrid architecture:

1. LLM-first query understanding with strict JSON output.
2. Fallback deterministic understanding only when LLM understanding fails.
3. Contextualization gate using understanding confidence to skip unnecessary rewrites.
4. SQL fast-path execution for known stable intents/entities (`user`, `asset` list/count).
5. Intent/entity merge to patch weak `intent_analysis` output before SQL generation.

## 4. Architecture Changes

### 4.1 New Service: QueryUnderstandingService
File: `app/services/query_understanding_service.py`

Responsibilities:

1. Build entity catalog from schema manifest aliases.
2. Classify intent/entity/follow-up/self-contained status from latest query + recent history.
3. Return normalized structure:
   - `intent`: `listing|aggregation|lookup|mutation|unknown`
   - `entities`: list of manifest table names
   - `is_self_contained`: boolean
   - `is_followup`: boolean
   - `confidence`: float in `[0,1]`
4. Use LLM as primary path.
5. Use fallback heuristics only if LLM fails (timeout, parse failure, invalid payload).

### 4.2 Contextualize Node Integration
File: `app/workflow/contextualize.py`

Flow now:

1. Call `QueryUnderstandingService.analyze(...)`.
2. If `is_self_contained == true` and confidence >= `0.7`, skip rewrite and pass original query.
3. Retain existing regex guard (`ContextualizationService.is_self_contained_operational_query`) as secondary safety fallback.
4. On deterministic slot-fill rewrite, re-analyze the rewritten query and store updated understanding.
5. On LLM rewrite path, re-analyze rewritten query and store understanding.

### 4.3 SQL Node Integration
File: `app/workflow/nodes/sql_node.py`

New behavior:

1. Read `query_understanding` from state, or compute if missing.
2. Merge understanding with `intent_analysis` using `_merge_intent_with_understanding(...)` when `intent_analysis` is weak/unknown.
3. Use merged analysis for:
   - clarification logic
   - deterministic fast-path decisions
   - prompt intent context

### 4.4 Deterministic SQL Templates for Asset
File: `app/services/schema_manifest.json`

Added templates:

1. `query_templates.asset.count`
2. `query_templates.asset.list`

These parallel existing user templates and reduce unnecessary LLM SQL generation for common asset queries.

### 4.5 Agent State Extension
File: `app/workflow/state.py`

Added state field:

1. `query_understanding: Dict[str, Any]`

This allows re-use of one understanding result across nodes in the same turn.

## 5. Detailed Implementation Notes

### 5.1 LLM Understanding Contract
`QueryUnderstandingService` prompt enforces valid JSON only, with explicit schema and entity catalog constraint.

Key validations after LLM response:

1. Parse JSON object from response body.
2. Normalize and bound intent to known enum.
3. Normalize entities to manifest table names only.
4. Clamp confidence to `[0.0, 1.0]`.

### 5.2 Fallback Behavior
Fallback is intentionally preserved for resilience.

Intent fallback mapping:

1. Aggregation: `count`, `how many`, `number of`.
2. Mutation: `create`, `add`, `new`, `insert`, `update`, `edit`, `modify`, `delete`, `remove`.
3. Listing: `list`, `show`, `fetch`, `display`.
4. Lookup: `get`, `find`, `details`, `lookup`.

Follow-up fallback checks:

1. Reference markers (`it`, `them`, `those`, `above`, etc.).
2. Very short response to previous assistant question.

### 5.3 Contextualization Guard
Existing method `is_self_contained_operational_query` remains as second guard to reduce regressions while transitioning.

Also adjusted rewrite prompt instruction to avoid carrying previous entity context when user explicitly switches to a different entity.

File: `app/services/contextualization_service.py`

## 6. Files Added and Modified

### 6.1 Added Files

1. `app/services/query_understanding_service.py`
2. `tests/test_query_understanding_service.py`
3. `docs/query_understanding_update.md`

### 6.2 Modified Files

1. `app/workflow/contextualize.py`
2. `app/workflow/nodes/sql_node.py`
3. `app/workflow/state.py`
4. `app/services/schema_manifest.json`
5. `app/services/contextualization_service.py`
6. `tests/test_contextualization_service.py`
7. `tests/test_sql_node.py`

## 7. Test Coverage

### 7.1 New/Updated Tests

1. `tests/test_query_understanding_service.py`
   - LLM-path structured classification via stub LLM.
   - Fallback path behavior when LLM fails.
   - `update` mutation fallback validation.
2. `tests/test_contextualization_service.py`
   - Self-contained query detection checks.
3. `tests/test_sql_node.py`
   - Asset list/count deterministic fast-path.
   - Intent merge behavior (`intent_analysis` + `query_understanding`).

### 7.2 Validation Command
Use:

```bash
PYTHONPATH=. pytest -q tests/test_query_understanding_service.py tests/test_contextualization_service.py tests/test_sql_node.py
```

Observed result during implementation: `19 passed`.

## 8. Runtime Flow (Post-Refactor)

1. User message enters graph.
2. `contextualize` node runs for multi-turn conversations.
3. Query understanding is computed.
4. If self-contained with high confidence, rewrite is skipped.
5. Router and downstream nodes use rewritten/original query.
6. SQL node merges intent with understanding.
7. Deterministic SQL fast-path is used when applicable.
8. Otherwise SQL LLM generation and validation flow continues.

## 9. Production Readiness Assessment
Current status: improved but not fully complete platform-wide.

Strengths:

1. Central understanding signal introduced.
2. Reduced context bleed risk.
3. More deterministic handling for common read queries.
4. Better resilience under model parse failures.

Remaining gaps:

1. Router does not yet consume `query_understanding` directly.
2. Table selection still includes heuristic logic in `TableSelectorService`.
3. Mutation path is not yet subtype-aware (`create/update/delete`) for safer guardrails.
4. No feature flag yet to enforce strict fail-closed behavior (disable fallback).

## 10. Recommended Next Steps

1. Integrate `query_understanding` into `app/workflow/router.py` to avoid duplicate interpretation layers.
2. Extend understanding schema with `mutation_subtype` (`create|update|delete`) and enforce validator constraints before execution.
3. Add structured decision logs for observability:
   - query
   - intent
   - entities
   - confidence
   - path used (`llm` vs `fallback`)
4. Add feature flag for fallback control:
   - `QUERY_UNDERSTANDING_FALLBACK_ENABLED=true|false`
5. Add integration tests for full conversation traces:
   - `list users` -> `list assets`
   - `assets` -> `only active`
   - `update asset ...` mutation safety checks

## 11. Notes for External Review
If this implementation is reviewed externally, reviewers should focus on:

1. Whether LLM JSON contract is strict enough for production parse safety.
2. Whether confidence threshold `0.7` is appropriate for skipping contextualization.
3. Whether fallback heuristics should remain enabled in production or be feature-gated.
4. Whether deterministic SQL templates should be expanded to more high-volume entities.

