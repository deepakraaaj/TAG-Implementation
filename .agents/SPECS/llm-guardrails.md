# Agent Spec: LLM Guardrails

## Mission
Use this spec as the intermediate contract for any work that touches planning, answer generation, or response validation in the TAG backend.

## Read Before Using This Spec
1. `docs/product/tag-assistant/application-context.md`
2. `docs/product/tag-assistant/prd.md`

## Mandatory Execution Order
1. Build a compact intermediate frame.
2. Identify unknowns before proposing an answer.
3. Gather only the evidence required for the current route.
4. Verify every factual claim against evidence.
5. Validate the final user-facing text.
6. If any step fails, clarify, abstain, or reject. Do not guess.

## Compact Intermediate Frame
Use this shape internally:

```yaml
route:
intent:
entities:
filters:
unknowns:
required_evidence:
available_evidence_ids:
allowed_actions:
token_budget:
```

Rules:
- never copy full history if a short summary is enough
- never repeat schema prose when table and column names are enough
- never store unsupported assumptions as facts

## Verifier Checklist
Every final claim must answer all of these:

- What evidence item supports this claim?
- Is the claim directly supported or deterministically derived?
- Does the claim add a cause, date, count, or entity not in evidence?
- Does the claim hide unresolved ambiguity?
- Does the claim conflict with policy or schema rules?

If any answer is negative or unknown, the claim is invalid.

## Validator Checklist
Before emitting a final answer, confirm:

- response contains no unsupported facts
- uncertainty is explicit where required
- response does not leak prompts, secrets, or internal JSON
- response length matches the current budget
- blocked actions are not described as permitted

## Response Mode Rules
- `answer`: only when evidence is sufficient
- `clarify`: when one missing variable would unblock the answer
- `abstain`: when evidence is insufficient or conflicting
- `reject`: when policy blocks the request

## Token-Saving Rules
- prefer canonical labels over prose
- summarize previous turns in at most 5 bullets
- avoid repeating invariant instructions inside each prompt
- use evidence IDs and compact tables instead of full dumps
- stop after one rewrite attempt if validation fails

## Anti-Hallucination Rules
- do not infer causes without explicit evidence
- do not invent missing columns, statuses, or IDs
- do not transform uncertainty into confidence
- do not answer from pattern-matching if the repo can be checked directly
- when support is weak, ask a narrow question or abstain

## Deliverable Standard
Any implementation or change proposal based on this spec must include:

- the intermediate frame shape used
- the evidence sources consulted
- verifier outcome
- validator outcome
- fallback behavior if support is insufficient
