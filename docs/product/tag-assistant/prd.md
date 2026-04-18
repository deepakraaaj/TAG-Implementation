# TAG Assistant Application PRD

Date: 2026-04-19
Owner: Backend Platform
Original PRD: 2026-03-11

**Implementation Status**: Guardrails Phase 1 & 2 COMPLETE. Verifier and Validator services deployed. Semantic retrieval with ChromaDB integrated. Domain manifests reorganized.

## Product Vision
The TAG application should act as a trustworthy operational assistant for business systems. Users should be able to ask questions, request reports, and trigger guided workflows in natural language without needing to know database structure, internal routing, or product navigation details.

The system should feel useful because it is:

- fast for common requests
- reliable for business-critical answers
- explicit when information is missing
- safe for read and write operations
- cheaper to extend to new domains over time

## Goal of the Application
The goal of the application is to convert natural-language requests into trustworthy business outcomes.

In practical terms, the application should:

- answer domain questions from validated data
- guide users through structured task flows
- generate reports and filtered result sets
- support navigation and follow-up actions in conversation
- reduce manual query writing and UI hunting
- expose uncertainty instead of guessing

## Primary Users
- Business users who want answers and actions through chat instead of manual navigation.
- Operations teams who need consistent status, counts, filters, and summaries.
- Platform engineers who need deterministic behavior, guardrails, and testable runtime contracts.
- Operators who need health, metrics, auditability, and safe rollout behavior.

## What the Application Solves Today
The current repository already solves several important parts of the product:

- FastAPI runtime with chat, query, health, readiness, and metrics endpoints
- LangGraph orchestration for chat, report, and SQL paths
- Redis-backed session history, state continuation, and idempotency replay
- SQL validation for protected tables, forbidden commands, and schema-aware checks
- domain-driven behavior through manifests and registry loading
- YAML-based guided flows for structured actions
- report and audit support services
- token-usage tracking and TOON-based compact previews

## Current Product Strengths
- Deterministic runtime already exists in important places, especially around routing, session behavior, and SQL validation.
- Domain behavior is increasingly configuration-driven instead of hard-coded in engine branches.
- The API contract is already stable enough to support streaming and buffered response modes.
- Health, metrics, and testing foundations are present.

## Current Problems and Gaps
The product is useful, but it still has gaps that limit trust, scale, and maintainability.

### Product Gaps
- LLM-generated responses can still add unsupported claims.
- Ambiguous prompts are not always turned into targeted clarifications.
- Evidence used to produce an answer is not always explicit and reviewable.
- Token usage can still be larger than necessary because context is repeated.

### Engineering Gaps
- Too much runtime behavior is still concentrated in a small number of services.
- Guardrail logic is distributed instead of represented as a single explicit pipeline.
- Domain onboarding is still heavier than it should be.
- Regression protection is stronger for SQL safety than for answer correctness.

### Operational Gaps
- We need better metrics for unsupported-claim rejection, abstention, and token savings.
- We need clearer failure modes for "not enough evidence" cases.
- We need rollout-safe enforcement of stricter validation.

## What We Have Already Solved
These are the areas the application can already claim as solved or mostly solved:

- basic chat/session lifecycle
- routing between chat, report, and SQL execution paths
- SQL execution safety gates
- protected resource blocking for unsafe SQL paths
- domain config validation at startup
- session continuation, follow-ups, pagination, and idempotent replay
- observability basics through health and metrics endpoints

## What We Need To Improve Now
The next major improvement is not "make the prompt better." The next improvement is to make the runtime more explicit, smaller, and more verifiable.

Priority improvements:

- add a compact intermediate contract before final response generation
- verify every factual claim against evidence
- validate final user-facing output before emit
- route ambiguous requests to clarification instead of guessing
- shrink prompt context to only the required evidence and state
- add metrics for verifier/validator outcomes
- add regression tests for typical LLM failure cases

## Strategic Improvement Theme
The key strategic theme for the next stage is:

`trusted answers with less token usage`

That means the application should become better by doing less free-form reasoning and more structured reasoning.

## Guardrail Strategy
This PRD proposes an application-level guardrail layer with three explicit stages:

1. Intermediate
Build a compact internal frame that captures route, intent, entities, filters, unknowns, required evidence, and allowed response modes.

2. Verifier
Check whether the planned or generated claims are actually supported by validated evidence and runtime policy.

3. Validator
Block final output that contains unsupported facts, hidden assumptions, policy leaks, or oversized responses.

## Functional Requirements
### FR-1: Compact Intermediate Contract
Before any final response is produced, the runtime must build a small structured representation of the request and current session state.

The contract must include:

- normalized intent
- route
- entities
- filters
- unknowns
- required evidence
- allowed actions
- token budget

### FR-2: Deterministic First
The system must prefer deterministic handling before additional LLM calls.

Examples:

- idempotency replay
- flow continuation
- navigation shortcuts
- schema checks
- SQL safety checks
- known follow-up patterns

### FR-3: Evidence-Bound Answers
Every factual claim in the final answer must map to evidence from:

- validated SQL results
- domain configuration
- runtime state
- user/request context

If support is missing, the claim must be removed, clarified, or converted into abstention.

### FR-4: Clarify Instead of Guess
If one missing variable can unblock the answer, the system must ask one targeted clarification question.

### FR-5: Abstain Instead of Invent
If the application does not have enough evidence, it must abstain with a short explanation and the next best action.

### FR-6: Preserve Existing Safety
The new guardrail layer must strengthen, not bypass, the existing SQL validator and domain/runtime safety rules.

### FR-7: Token Efficiency
The application must reduce token waste by:

- summarizing prior state into a fixed compact frame
- including only relevant schema and evidence
- avoiding repeated instruction prose
- limiting response size by route and confidence
- skipping unnecessary LLM stages

### FR-8: Observability
The application must emit metrics and logs for:

- verifier pass/fail
- validator pass/fail
- clarification count
- abstention count
- estimated token savings
- unsupported-claim rejection count

## Typical LLM Problems We Want To Solve
This initiative is specifically aimed at recurring LLM failure patterns:

- hallucinated facts
- invented causes and explanations
- overconfident answers under missing context
- prompt bloat from repeated history and schema text
- inconsistency between internal plan and final answer
- unsafe fallback behavior when the system is unsure

## Important Constraint
No architecture can truthfully guarantee "never hallucinate" in the absolute sense.

What we can build is a stronger practical guarantee:

- unsupported claims should be blocked before user-visible output
- uncertainty should be surfaced instead of hidden
- ambiguous requests should be clarified
- unsupported cases should fail closed

## How We Can Improve After This Phase
Once the intermediate/verifier/validator pipeline exists, the next improvements become easier:

- domain onboarding from schema plus business vocabulary
- smaller route-specific prompts
- better replay and regression harnesses
- confidence-aware response shaping
- stricter write-path authorization checks
- more useful audit trails for why an answer was allowed or blocked

## Future Roadmap
### Near Term
- add compact intermediate contracts
- add verifier and validator reports
- enforce guardrails first on SQL-backed answers
- measure token savings and blocked outputs

### Mid Term
- extend guardrails to chat and report routes
- improve claim extraction and evidence mapping
- add benchmark suites for ambiguity and unsupported claims
- make response behavior more consistent across domains

### Long Term
- make domain onboarding mostly generation plus review
- split domain artifacts cleanly into generated and manual layers
- support replay-based quality scoring before rollout
- treat the runtime as a reusable domain-assistant platform, not a single-domain implementation

## Success Metrics
- 0 unsupported claims accepted in guardrail regression fixtures
- >= 30% reduction in prompt token volume on guarded paths relative to baseline
- 100% of ambiguous benchmark prompts end in clarification or abstention, not guessing
- no regression in existing `/chat` terminal result contract
- measurable increase in deterministic handling for common requests

## Non-Goals
- Perfect factual correctness under all external conditions
- Replacing the whole runtime with a prompt-only architecture
- Hiding uncertainty to make answers sound more polished
- Solving every planning problem in a single release

## Acceptance Criteria
- The PRD clearly states the application goal, current solved areas, gaps, improvements, and future direction.
- The guardrail strategy is defined as part of the product direction, not as a standalone prompt trick.
- Engineers can implement from this document without re-deciding core behavior at coding time.
