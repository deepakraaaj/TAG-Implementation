# Domain Runtime Business Requirements

Date: 2026-03-09
Owner: Backend Platform
Document Type: BRD

## Purpose
Define the business requirements for evolving the current assistant into a reusable domain-runtime platform that can onboard new domains from database metadata and reverse-engineered schema inputs with minimal core-code changes.

## Executive Summary
The business need is to reduce the cost, time, and engineering risk of onboarding new domains into the assistant. The target operating model is schema-driven domain generation, typed validation, deterministic-first runtime planning, and controlled human review for uncertain mappings.

## Problem Statement
The current system is functional but too tightly coupled to domain-specific behavior and too dependent on manually curated config. This creates four business problems:

- domain onboarding is slow and engineering-heavy
- configuration files are difficult to maintain and review
- parser/runtime behavior can drift when logic is split between code and config
- scaling the assistant to new business domains requires repeated platform work

## Business Goal
Create a platform where a new domain can be introduced by providing:

- `db_url`
- reverse-engineered schema input
- optional business vocabulary/examples

and the system can generate, validate, and run a domain package with limited manual review.

## Desired Business Outcome
- Faster onboarding for new domains.
- Lower core-runtime change frequency.
- Cleaner and reviewable configuration artifacts.
- Better accuracy on routine list/count/filter requests.
- Lower LLM spend and lower operational overhead on deterministic flows.

## Objectives
- Reduce domain onboarding effort.
- Reduce hardcoded business behavior in runtime code.
- Improve query accuracy and consistency.
- Lower runtime latency and LLM usage for deterministic requests.
- Make generated configuration reviewable, non-destructive, and reusable.

## Stakeholders
- Backend Platform Team
- Product Team
- QA / Release Team
- Domain Onboarding Team
- Operations / Support

## Assumptions
- Reverse-engineered schema inputs are available and reasonably accurate.
- Domain onboarding teams can review uncertain mappings before production rollout.
- Existing maintenance behavior must remain stable while the platform evolves.
- Deterministic planning can cover a large share of routine user requests.

## In Scope
- Domain config redesign
- Generator agent / scaffold pipeline
- Typed `DomainSpec`
- Runtime refactor toward generic services
- Manifest-first SQL planning
- Generated vs manual override model
- Validation, test replay, and rollout controls

## Out of Scope
- Full autonomous domain onboarding with no human review
- "Perfect" natural-language accuracy
- Replacing all SQL execution logic with LLM generation
- UI redesign outside the required workflow/config outputs

## Guiding Principles
- Business semantics belong in domain artifacts, not runtime branches.
- Weak inferences must be reviewable, not silently accepted.
- Generated config must be replaceable without overwriting manual decisions.
- Runtime safety is more important than aggressive guessing.

## Business Requirements
1. The platform must support onboarding new domains without modifying core planner logic for normal cases.
2. The platform must generate structured domain artifacts from schema inputs.
3. The platform must separate generated outputs from manual overrides.
4. The runtime must validate domain configuration before serving requests.
5. The runtime must prefer deterministic parsing and manifest-driven SQL planning over LLM inference whenever possible.
6. The system must surface ambiguity instead of silently guessing in high-risk cases.
7. The platform must support replay-based regression testing for production-like prompts.
8. The solution must reduce operational overhead for adding future domains.

## Functional Requirements
- Accept database metadata and reverse-engineered schema input.
- Build a canonical schema graph.
- Infer candidate table roles, field roles, joins, display columns, and domain config sections.
- Emit a typed `DomainSpec`.
- Render split configuration files.
- Produce a review report with confidence and `needs_review` markers.
- Load merged generated/manual config into the runtime.
- Execute SQL using generic planner/orchestrator services.

## Non-Functional Requirements
- Accuracy: deterministic-first with measurable regression coverage.
- Performance: reduced LLM usage and lower latency for common paths.
- Maintainability: config must be modular and reviewable.
- Reusability: new domains should reuse the same core runtime.
- Safety: invalid or incomplete domain config must fail fast.
- Auditability: generated outputs and manual overrides must be traceable.

## Success Metrics
- Time to onboard a new domain decreases materially versus manual setup.
- Core-code changes required per new domain approach zero for normal onboarding.
- LLM calls per deterministic query decrease.
- Invalid-SQL and wrong-entity regressions decrease.
- Configuration review time decreases due to cleaner split artifacts.

## Delivery Guardrails
- Migration must be incremental and regression-backed.
- New config structures must remain understandable to engineers and reviewers.
- Regeneration must not destroy curated overrides.
- Runtime changes must ship behind tests and measurable exit criteria.

## Acceptance Criteria
- A new domain can be scaffolded from schema inputs into a valid domain package.
- The generated output passes config validation.
- The runtime can answer basic list/count/filter queries for the generated domain.
- Manual overrides can be applied without being overwritten by regeneration.
- Replay tests can be run against the new domain package before rollout.

## Constraints
- Schema alone cannot reliably infer all business vocabulary.
- Human review remains necessary for uncertain mappings and business phrasing.
- Existing runtime behavior must remain stable during migration.
- Refactor must proceed incrementally, not as a big-bang rewrite.

## Dependencies
- domain registry validation support
- schema introspection and reverse-engineering input quality
- replay-test infrastructure
- domain manifest and config merge strategy

## Risks
- Over-generation of weak or misleading config
- Silent mismatch between generated config and runtime expectations
- Regression in existing maintenance-domain behavior
- Excessive complexity if generated/manual merge rules are unclear

## Mitigations
- Typed validation and startup failure for invalid config
- `needs_review` markers instead of silent guesses
- Golden tests and prompt replay before rollout
- Small phased delivery with explicit exit criteria

## Current Status
- [x] Initial SQL-builder behavior has been pushed substantially toward config-driven execution.
- [x] Recent parsing regressions have dedicated test coverage.
- [x] Overview, BRD, phase plan, and target-state architecture docs are available.
- [x] Typed config validation is now enforced for the current core runtime contract.
- [x] `DomainSpec` now exists and is built in the registry.
- [x] Generated/manual artifact layering is now supported in the registry loader.
- [ ] Existing domain packages have not yet been fully migrated to split artifacts.
- [ ] Domain generation from `db_url` plus reverse-engineered schema input is still pending.

## Companion Documents
- `docs/architecture/domain-runtime-overview.md`
- `docs/architecture/domain-runtime-phase-plan.md`
- `docs/architecture/domain-runtime-target-state.md`
