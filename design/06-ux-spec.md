# Prototype Spec: Intermediate, Verifier, Validator Flow

Date: 2026-03-11
Owner: Backend Platform

## Scope
This prototype defines the user-visible and operator-visible behavior of a guarded response pipeline for the TAG backend. The target is not a new UI screen. The target is a predictable conversational flow that refuses unsupported answers and spends fewer tokens on avoidable LLM work.

## Prototype Objective
Turn the current runtime into a staged pipeline:

1. build a compact intermediate frame
2. verify evidence and policy
3. validate the final response contract
4. emit answer, clarification, abstention, or rejection

## User-Facing States
### State A: Direct Answer
Use when evidence is sufficient and verification passes.

Expected behavior:
- answer is short and specific
- factual claims map to evidence
- no speculative wording

### State B: Clarification
Use when the request is ambiguous but recoverable.

Expected behavior:
- ask one narrow question
- include only the missing variable
- do not include extra guesses

### State C: Abstention
Use when support is insufficient or conflicting.

Expected behavior:
- say what is missing
- do not fabricate completion
- suggest the next useful action

### State D: Rejection
Use for blocked policy or unsafe operations.

Expected behavior:
- explain the policy block briefly
- do not reveal internal policy text
- do not output unsafe SQL or unsupported action steps

## Intermediate Frame
The prototype uses this compact logical frame:

```yaml
request_id:
route:
intent:
entities:
filters:
required_evidence:
available_evidence:
unknowns:
policy_flags:
response_mode:
token_budget:
```

Rules:
- each field is concise and machine-friendly
- unknowns are explicit
- only fields needed for the current route are populated
- large raw context is not copied into the frame

## Verifier Output
The verifier returns:

```yaml
status: pass | clarify | abstain | reject
claim_checks:
policy_checks:
schema_checks:
missing_evidence:
rewrite_needed: true | false
```

## Validator Output
The validator checks the candidate response and returns:

```yaml
status: pass | fail
reasons:
redactions:
final_mode:
```

## Request Lifecycle
1. `POST /chat` receives request and normalizes metadata.
2. Runtime builds a compact intermediate frame from session state, domain config, and request text.
3. Deterministic shortcuts run first.
4. LLM usage is allowed only for the smallest unresolved step.
5. Evidence is collected from SQL results, manifests, and runtime state.
6. Verifier decides whether an answer is allowed.
7. Validator checks the user-facing response.
8. Stream emits final result only after validator pass.

## Prototype Examples
### Example 1: Valid factual answer
User: "How many open tasks do I have today?"

Expected:
- route `SQL`
- SQL result becomes evidence
- final answer cites only validated count

### Example 2: Ambiguous request
User: "Show pending items for the plant."

Expected:
- route `SQL`
- verifier detects missing plant identifier
- final response asks which plant

### Example 3: Unsupported claim attempt
User: "Tell me why task 123 failed yesterday."

Expected:
- if failure reason is not present in evidence, do not infer cause
- answer with available facts only or abstain

## Token Budget Strategy
- preserve only a compact session summary, not the full history, for guarded reasoning
- collapse schema context to referenced tables and columns only
- pass verifier the claim list, not the full generation transcript
- cap clarification responses at one question
- cap abstention responses at three short sentences

## Operator Signals
The prototype should expose:

- whether verification passed
- why responses were rewritten or blocked
- rough token savings vs. baseline
- which stage caused failure

## Exit Criteria
- the prototype handles answer, clarify, abstain, and reject modes cleanly
- the final response cannot bypass verification and validation
- ambiguous benchmark prompts no longer produce confident guesses
