# Domain Runtime Overview

Date: 2026-03-09
Owner: Backend Platform

## Vision
Turn the current assistant into a reusable domain-runtime platform where domain onboarding is primarily generation and review work, not core-engine customization.

The platform should support:

- generic runtime code
- domain behavior declared through validated config
- onboarding from `db_url` plus a reverse-engineered schema input
- clean separation between generated artifacts and manual overrides
- deterministic runtime behavior with AI used for inference, repair, and review

## Why This Exists
The current assistant works, but its behavior is still spread across code, config, and domain-specific assumptions. That creates three recurring problems:

- onboarding a new domain is too engineering-heavy
- config becomes hard to review when everything lives in large JSON files
- behavior drifts when fallback logic is hidden in runtime code

## Core Principles
- Deterministic first: known patterns should be resolved without LLM dependence.
- Explicit over implicit: ambiguity should trigger clarification, not guessing.
- Config over code: business semantics belong in domain artifacts, not engine branches.
- Generated plus curated: generation should accelerate setup, while human review protects correctness.
- Fail fast: invalid domain config should be rejected at startup.

## Operating Model
1. Inputs
- `db_url`
- reverse-engineered schema file
- optional business vocabulary, sample prompts, or domain notes

2. Domain generator
- introspects schema metadata
- builds a canonical schema graph
- produces a typed `DomainSpec`
- marks uncertain inferences as `needs_review`

3. Artifact renderer
- writes split config into `generated/`
- preserves manual decisions in `manual/`
- emits a review report with confidence and unresolved items

4. Runtime
- merges generated and manual artifacts
- validates the final `DomainSpec`
- runs generic planner/orchestrator services only

## Artifact Model
The long-term configuration model should be split into focused artifacts instead of one large domain JSON.

Recommended artifact set:

- `schema_manifest.json`
- `entity_behavior.json`
- `sql_builder.json`
- `lookups.json`
- `messages.json`
- `workflows.json`
- `overrides.json`

Recommended folder structure:

- `domains/<domain>/generated/`
- `domains/<domain>/manual/`

## Lifecycle
1. Generate a draft domain package from schema inputs.
2. Review `needs_review` items and apply curated overrides.
3. Validate the merged `DomainSpec`.
4. Run smoke tests and replay tests.
5. Enable the domain in runtime without changing core planner code.

## Generator Guardrails
- Never guess silently.
- Prefer schema-derived facts over prompt-based guesses.
- Treat business vocabulary as candidate input, not guaranteed truth.
- Emit confidence and review markers for weak mappings.
- Keep regeneration idempotent and non-destructive.

## Current Progress
- [x] SQL-builder query behavior moved substantially toward config-driven execution.
- [x] Negation parsing and assignee misparse fixes were moved out of code-specific branches.
- [x] Broad SQL-builder unit coverage is passing after the recent refactor.
- [x] Domain runtime overview, BRD, phase plan, and target-state documents now exist.
- [x] Registry-level typed validation now enforces core domain config and manifest sections at startup.
- [x] `DomainSpec` now exists and is built as the validated registry source of truth.
- [x] Registry loading now supports `generated/` and `manual/` domain artifact layering.
- [ ] Existing domains are still mostly stored in legacy single-file artifacts and have not been fully split yet.
- [ ] Runtime consumers still often operate on dict-shaped config instead of spec-oriented services.
- [ ] Runtime responsibilities are still too concentrated in `sql_builder_node.py`.

## Current Implementation State
- The registry validates merged domain artifacts before runtime use.
- The registry can load legacy files, generated files, and manual overrides together.
- `get_config_section()` now resolves through the validated `DomainSpec`.
- The generator agent and full domain-package migration are still not implemented.

## Constraint
Perfect natural-language accuracy is not a realistic target. The correct target is:

- deterministic where possible
- explicit where ambiguous
- measurable in production
- regression-protected through replay and unit tests

## Companion Documents
- `docs/architecture/domain-runtime-business-requirements.md`
- `docs/architecture/domain-runtime-phase-plan.md`
- `docs/architecture/domain-runtime-target-state.md`
