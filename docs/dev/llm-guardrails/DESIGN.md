# LLM Guardrails Detailed Design

Date: 2026-03-11
Owner: Backend Platform

## Design Summary
The guardrail layer is inserted between intent resolution and final response emission. It does not replace existing runtime services. It adds a compact contract and two explicit gates:

- verifier gate: is the answer supportable?
- validator gate: is the final text safe to emit?

## Proposed Runtime Placement
### Chat Path
- `ChatService` creates a session-aware summary frame.
- `ChatNode` or route-specific handlers may answer directly for deterministic cases.
- If an LLM response is needed, it receives only the compact frame and allowed evidence.
- Verifier runs on the candidate answer.
- Validator runs on the user-facing output.

### SQL Path
- Existing graph remains:
  - `intent`
  - `sql_build`
  - `sql_validate`
  - `sql_execute`
  - `respond`
- Guardrails extend this path:
  - build `IntermediateFrame` before `sql_build`
  - assemble `EvidenceBundle` after `sql_execute`
  - verify claims before `respond`
  - validate response text inside or immediately before `respond`

## Suggested File Layout
Potential new modules:

- `app/services/guardrails/intermediate_service.py`
- `app/services/guardrails/evidence_service.py`
- `app/services/guardrails/verifier_service.py`
- `app/services/guardrails/validator_service.py`
- `app/services/guardrails/models.py`

This keeps the current domain, data, and assistant services intact.

## Sequence
1. Normalize request and recover session state.
2. Build `IntermediateFrame`.
3. Run deterministic gates:
- route shortcuts
- cache replay
- flow continuation
- schema allow-list preparation
4. Call LLM only for unresolved planning or wording.
5. Assemble evidence.
6. Run verifier.
7. If verifier status is:
- `pass`: continue
- `clarify`: emit clarification
- `abstain`: emit abstention
- `reject`: emit rejection
8. Run validator on final text.
9. Emit final result and metrics.

## Verification Logic
The verifier should operate on claim units, not on full paragraphs.

Claim extraction rules:
- split candidate response into atomic factual claims
- ignore purely procedural phrases
- track counts, dates, entities, statuses, and causal statements separately

Support rules:
- a claim is supported only if an evidence item explicitly covers it
- derived statements are allowed only when the derivation rule is deterministic
- causal claims require explicit evidence and are never inferred from correlation

## Validation Logic
The validator checks:
- unsupported nouns, dates, counts, and causes
- presence of mandatory uncertainty markers
- leakage of prompt text or raw internal JSON
- response length against selected token budget

If validation fails:
1. rewrite from verifier-approved claim set
2. revalidate
3. abstain if still failing

## Token-Saving Design
- keep a rolling session summary capped to a fixed shape
- include only referenced schema objects in LLM context
- pass structured evidence IDs instead of raw row dumps where possible
- skip generation entirely for simple navigation or known deterministic responses
- use terse internal labels rather than repeating prose descriptions

## Anti-Hallucination Design Principles
- evidence before explanation
- reject rather than guess
- one clarification question instead of multi-turn fishing
- deterministic derivations only
- no final answer path that bypasses verification

## Pseudocode
```text
frame = build_intermediate(request, session, domain)
shortcut = try_deterministic_path(frame)
if shortcut:
    return shortcut

candidate = generate_minimal_candidate(frame)
evidence = assemble_evidence(frame, execution_results, domain, session)
verification = verify(candidate, evidence, policy)

if verification.status != "pass":
    return emit_non_answer(verification.status, verification)

validated = validate(candidate, verification)
if validated.status == "fail":
    candidate = rewrite_from_supported_claims(verification)
    validated = validate(candidate, verification)
    if validated.status == "fail":
        return emit_non_answer("abstain", validated)

return emit_answer(candidate, verification, validated)
```

## Rollout Plan
### Phase 1
- add models and internal reports only
- emit metrics without changing user-visible behavior

### Phase 2
- enable verifier enforcement on SQL route
- keep chat route in observe mode

### Phase 3
- enable validator enforcement on all final responses
- add regression fixtures for ambiguity and unsupported claims

## Risks
- Over-blocking can reduce usefulness if evidence mapping is too strict.
- Poor claim extraction can create false failures.
- Verifier metadata can grow unless contracts stay compact.

## Mitigations
- start with small claim taxonomy
- add mode-specific budgets
- review blocked responses in metrics/logs before wide rollout
