# LLM Guardrails Module Spec

Date: 2026-04-19
Owner: Backend Platform
Original Spec Date: 2026-03-11

**Status**: IMPLEMENTED Phase 1 & 2 as of 2026-04-19. Verifier and Validator services are active in runtime.

## Objective
Specify the backend modules needed to add an intermediate contract, verifier, and validator to the current TAG runtime while preserving the existing FastAPI and LangGraph flow.

## Current Integration Points
- `app/services/chat/service.py`
- `app/assistant/orchestration/graph.py`
- `app/assistant/nodes/core/response_node.py`
- `app/assistant/nodes/sql/sql_validate_node.py`
- `app/services/data/sql_validator.py`
- `app/services/core/token_usage_service.py`
- `app/services/observability/metrics_service.py`

## New Logical Modules
### 1. Intermediate Contract Builder
Purpose:
- compress request, session, and route state into a minimal execution frame

Inputs:
- message text
- selected route or candidate routes
- session summary
- user context
- domain capabilities

Outputs:
- `IntermediateFrame`

Required fields:
- `route`
- `intent`
- `entities`
- `filters`
- `unknowns`
- `allowed_actions`
- `required_evidence`
- `token_budget`

### 2. Evidence Assembler
Purpose:
- normalize evidence from SQL, manifests, cached state, and flow state into a consistent structure

Outputs:
- `EvidenceBundle`

Evidence types:
- `sql_rowset`
- `domain_config`
- `runtime_state`
- `user_context`

### 3. Claim Verifier
Purpose:
- compare planned or candidate claims against the evidence bundle and runtime policy

Outputs:
- `VerificationReport`

Checks:
- claim coverage
- schema coverage
- policy coverage
- ambiguity coverage
- allowed response mode

### 4. Response Validator
Purpose:
- block unsupported final text before emit

Outputs:
- `ValidationReport`

Checks:
- no unsupported claims
- no hidden assumptions
- no policy leaks
- no oversized response for selected mode
- no raw internal artifacts in user text

### 5. Token Budget Policy
Purpose:
- choose prompt size and response size limits per route and confidence level

Outputs:
- `TokenBudget`

## Data Contracts
### IntermediateFrame
```json
{
  "request_id": "uuid",
  "route": "CHAT|SQL|REPORT",
  "intent": "string",
  "entities": ["string"],
  "filters": {"key": "value"},
  "unknowns": ["string"],
  "required_evidence": ["string"],
  "allowed_actions": ["answer", "clarify", "abstain", "reject"],
  "token_budget": {"prompt_max": 0, "response_max": 0}
}
```

### EvidenceBundle
```json
{
  "items": [
    {
      "id": "string",
      "type": "sql_rowset|domain_config|runtime_state|user_context",
      "source": "string",
      "claims_supported": ["string"]
    }
  ]
}
```

### VerificationReport
```json
{
  "status": "pass|clarify|abstain|reject",
  "missing_evidence": ["string"],
  "claim_results": [{"claim": "string", "supported": true}],
  "policy_results": [{"check": "string", "passed": true}],
  "rewrite_needed": true
}
```

### ValidationReport
```json
{
  "status": "pass|fail",
  "reasons": ["string"],
  "redactions": ["string"],
  "final_mode": "answer|clarify|abstain|reject"
}
```

## Hard Invariants
- Final user-visible claims must be evidence-backed or explicitly labeled uncertain.
- Verification must run before final emit.
- Validation failure must block output and force rewrite, abstention, or rejection.
- The system must prefer clarification over guessing.
- Existing SQL validation remains mandatory and is not bypassed by the new layer.

## Failure Modes
- Missing schema detail: ask clarification or abstain.
- Conflicting evidence: abstain and expose conflict in internal metadata.
- Policy failure: reject.
- Validator failure after generation: rewrite once, then abstain if still invalid.
- Token budget exhaustion: degrade to shorter answer or clarification, never to unsupported completion.

## Required Metrics
- `guardrails_verifier_pass_total`
- `guardrails_verifier_fail_total`
- `guardrails_validator_fail_total`
- `guardrails_abstain_total`
- `guardrails_clarify_total`
- `guardrails_estimated_tokens_saved_total`

## Testing Requirements
- unit tests for each report type
- regression tests for ambiguous prompts
- regression tests for unsupported claim rejection
- stream contract tests to ensure terminal result behavior is preserved
- SQL route tests proving the guardrail layer does not weaken `SQLValidatorService`

## Acceptance
This module spec is complete when implementation can start without inventing new contracts at coding time.
