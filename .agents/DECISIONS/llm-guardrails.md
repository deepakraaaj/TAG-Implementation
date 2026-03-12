# Decisions: LLM Guardrails

Date: 2026-03-11

## Accepted
### D-1: Fail closed
If support is insufficient, the runtime must clarify, abstain, or reject. It must not guess.

### D-2: Verification before final emit
The verifier is a mandatory gate. A candidate answer is not final output.

### D-3: Existing SQL validation stays authoritative
`SQLValidatorService` remains the safety authority for SQL structure and protected resources.

### D-4: Compact contracts over prompt prose
The guardrail layer will prefer typed frames and short evidence references to reduce token usage.

## Open
### O-1: Claim extraction location
Decide whether claim extraction belongs in the verifier service or response generation service.

### O-2: Rewrite strategy
Decide whether failed validation should use rule-based rewrite only or allow a constrained LLM rewrite pass.

### O-3: Metadata exposure
Decide how much verification detail to include in terminal payloads versus internal logs only.

## Rejected
### R-1: "Never hallucinate" by prompt wording alone
Prompt wording is not sufficient. The design uses fail-closed verification and validation instead.

### R-2: Full transcript verification
Verifying raw transcripts is too token-expensive. Verify compact claim units and evidence instead.
