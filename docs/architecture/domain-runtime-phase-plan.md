# Domain Runtime Phase Plan

Date: 2026-03-09
Owner: Backend Platform

## Execution Rules
- Complete each phase with tests and measurable outputs before moving on.
- Do not reintroduce hidden code fallbacks while externalizing behavior.
- Prefer small PR-sized changes over a large rewrite.
- Treat `DomainSpec` as the long-term integration seam between generation and runtime.

## Already Completed Foundation Work
- [x] SQL-builder query behavior moved substantially toward config-driven execution.
- [x] Recent negation and assignee parsing bugs were removed from hardcoded code branches.
- [x] Regression tests exist for the recent parsing fixes.
- [x] Domain-runtime overview, BRD, phase plan, and target-state documents are in place.
- [x] Core SQL-builder heuristics such as short-query intent skip, user-suggestion thresholds, and unfiltered-select limits were moved into config.

## Phase 0: Baseline Freeze
Objective: lock current behavior before deeper refactors.

- [ ] Build a regression corpus from real prompts and known failure cases.
- [ ] Add golden tests for routing, filter parsing, negation, assignee resolution, date scope, and count-vs-list behavior.
- [ ] Record baseline latency, LLM call count, ambiguity rate, and fallback rate.

Deliverables:
- prompt replay corpus
- baseline metrics snapshot
- regression suite covering current maintenance behavior

Exit criteria:
- Every later refactor is evaluated against the same corpus and metrics.

## Phase 1: Config Contract
Objective: make configuration strict, typed, and fail-fast.

- [x] Define strict Pydantic models for the current core runtime contract.
- [x] Validate `entity_behavior`, `sql_builder`, `user_lookup`, `location_lookup`, `select_workflow`, and manifest sections at startup.
- [x] Remove silent fallback behavior for those required config sections.
- [ ] Extend typed validation to the remaining optional or secondary domain sections.

Deliverables:
- typed config models
- startup validation path in the domain registry
- clear validation errors for missing or invalid sections

Exit criteria:
- Config is treated like code, not loose JSON.

## Phase 2: Config Restructure
Objective: split large domain config into maintainable artifacts.

- [ ] Replace oversized domain JSON usage with split config sections across shipped domains.
- [x] Introduce `generated/` and `manual/` layering support in the domain registry.
- [x] Define merge rules so generated config can be replaced safely.
- [ ] Migrate existing domain packages from legacy single-file layout to the split layout.

Deliverables:
- split artifact structure
- merge strategy for generated plus manual layers
- migration path for existing domains

Exit criteria:
- Regeneration does not overwrite manual fixes.

## Phase 3: DomainSpec
Objective: create one typed source of truth between generation and runtime.

- [x] Create a typed `DomainSpec` as the canonical intermediate model.
- [x] Normalize merged generated and manual config into `DomainSpec` in the registry.
- [x] Route registry config access through validated `DomainSpec`.
- [ ] Move more runtime consumers from dict access to spec-oriented service APIs.
- [ ] Render and persist fully split runtime artifacts from `DomainSpec`.

Deliverables:
- `DomainSpec` models
- normalization pipeline
- runtime adapter consuming validated spec objects

Exit criteria:
- Runtime consumes validated `DomainSpec`, not ad hoc dicts.

## Phase 4: Generator Agent
Objective: scaffold domains from schema input instead of handwritten config.

- [ ] Build a schema introspection pipeline from `db_url`.
- [ ] Parse reverse-engineered schema files into a canonical schema graph.
- [ ] Infer table roles, joins, likely tenant scope, display fields, date/status/user fields, and starter query templates.
- [ ] Emit confidence scores and `needs_review` markers instead of silent guesses.
- [ ] Generate split config files from the inferred `DomainSpec`.

Deliverables:
- schema graph builder
- domain generation CLI
- review report with unresolved mappings

Exit criteria:
- A new domain can be scaffolded from schema inputs with a review report.

## Phase 5: Runtime Decomposition
Objective: replace the current monolithic node with reusable services.

- [ ] Split `app/assistant/nodes/sql/sql_builder_node.py` into reusable services.
- [ ] Introduce `QueryClassifier`.
- [ ] Introduce `FilterNormalizer`.
- [ ] Introduce `EntityResolver`.
- [ ] Introduce `DisambiguationService`.
- [ ] Introduce `SQLPlanner`.
- [ ] Introduce `PromptPayloadBuilder`.
- [ ] Introduce `PolicyValidator`.

Deliverables:
- thin orchestrator node
- isolated service modules with unit coverage
- clear runtime ownership boundaries

Exit criteria:
- The node becomes a thin orchestrator.

## Phase 6: Manifest-First SQL Planning
Objective: move planning semantics out of code and into manifest roles.

- [ ] Push SQL planning decisions into manifest/config wherever possible.
- [ ] Declare field roles, relation roles, display columns, list/count templates, and anti-join templates explicitly.
- [ ] Reduce dynamic SQL assembly to a generic fallback path only.

Deliverables:
- richer schema manifest
- template-driven list/count planning
- reduced column-name guessing in runtime code

Exit criteria:
- SQL planning is driven by manifest semantics instead of code guessing.

## Phase 7: Accuracy Hardening
Objective: improve correctness by reducing silent assumption.

- [ ] Add ambiguity-first behavior instead of silent guessing.
- [ ] Replay production prompts as regression tests.
- [ ] Track wrong-entity, wrong-user, wrong-date, and invalid-SQL cases.
- [ ] Add test coverage for negative queries, follow-ups, and multi-turn disambiguation.

Deliverables:
- replay harness
- ambiguity rules
- accuracy dashboard metrics

Exit criteria:
- Accuracy is measured and regressions are reproducible.

## Phase 8: Lightweight Runtime
Objective: keep the runtime fast and cheap once the architecture is cleaner.

- [ ] Cache validated domain config and compiled regex.
- [ ] Minimize LLM calls for deterministic queries.
- [ ] Benchmark classify, normalize, resolve, plan, and render stages separately.
- [ ] Reduce prompt size by passing only relevant manifest slices.

Deliverables:
- stage benchmarks
- caching strategy
- deterministic routing gates for LLM usage

Exit criteria:
- Lower latency and fewer LLM calls with no accuracy loss.

## Phase 9: Plug-and-Play Domain Onboarding
Objective: make new-domain setup generation-and-review work only.

- [ ] Add a CLI such as `generate_domain --db-url ... --schema-file ...`.
- [ ] Generate domain folders with `generated/` and `manual/` structure.
- [ ] Produce a review report for unresolved mappings.
- [ ] Validate and smoke-test the generated domain before runtime use.

Deliverables:
- domain onboarding CLI
- generated domain scaffold
- smoke-test workflow for generated packages

Exit criteria:
- New domains are onboarded through generation and review, not engine edits.

## Phase 10: Rollout Discipline
Objective: keep the migration safe while behavior stays live.

- [ ] Roll out config validation behind tests first.
- [ ] Ship service decomposition in small PR-sized steps.
- [ ] Gate each phase behind passing replay and unit coverage.
- [ ] Add release notes for runtime-contract changes.

Deliverables:
- staged rollout checklist
- release-note template for domain-runtime changes
- rollback expectations for high-risk phases

Exit criteria:
- Refactor velocity stays high without destabilizing behavior.

## Recommended Execution Order
- [x] Foundation documentation and initial config-driven refactor
- [x] Phase 1 core validation
- [x] Phase 2 registry layering support
- [x] Phase 3 registry-backed `DomainSpec`
- [ ] Phase 0
- [ ] Finish remaining Phase 1 scope
- [ ] Finish remaining Phase 2 scope
- [ ] Finish remaining Phase 3 scope
- [ ] Phase 4
- [ ] Phase 5
- [ ] Phase 6
- [ ] Phase 7
- [ ] Phase 8
- [ ] Phase 9
- [ ] Phase 10
