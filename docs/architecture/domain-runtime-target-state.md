# Plug-And-Play AI-Native Target State

Date: 2026-03-09
Owner: Backend Platform

## Target
Build a chatbot platform that is:

- plug-and-play
- more accurate
- lighter at runtime
- AI-native in generation and review
- reusable across domains

## Summary
The target system is a domain-runtime platform, not a single-domain assistant. Runtime code stays generic, domain behavior is loaded from validated artifacts, and AI is used where it adds leverage rather than where deterministic logic should exist.

## What Plug-And-Play Means
- A new domain is added from schema inputs, not by editing planner code.
- Domain-specific behavior is loaded from validated config.
- Generated config is replaceable.
- Manual overrides are preserved.

## What More Accurate Means
- Deterministic parser and planner handle known cases first.
- Manifest-declared semantics replace code guessing.
- Ambiguous requests trigger clarification instead of silent assumption.
- Production replay tests continuously guard behavior.

## What Lightweight Means
- LLM is used only when deterministic logic cannot resolve the request.
- Regex and config are precompiled/cached.
- Schema slices are passed selectively.
- SQL templates are preferred over prompt-heavy generation.

## What AI-Native Means
- AI helps generate `DomainSpec` from schema.
- AI suggests aliases, examples, and business phrasing.
- AI explains uncertain mappings and proposes review candidates.
- AI does not own deterministic runtime behavior.

## Platform Shape
- `DomainSpec` is the canonical source of truth.
- `generated/` stores scaffolded output from the domain generator.
- `manual/` stores curated human overrides and corrections.
- a merge step produces validated runtime-ready config.
- generic services consume only validated config and manifests.

## Current vs Target
- Current: the registry now builds and validates a typed `DomainSpec`.
- Current: layered `generated/` and `manual/` loading is supported in the registry.
- Current: many runtime consumers still use dict-shaped access patterns.
- Target: runtime services should consume spec-oriented models directly, with generator-produced split artifacts as the normal path.

## Request Lifecycle
1. classify the request deterministically when possible
2. resolve entity, filters, and scope from validated config
3. plan SQL from manifest roles and templates
4. ask for clarification when ambiguity remains
5. use LLM fallback only for cases deterministic logic cannot safely resolve

## Runtime Principles
- No domain semantics in engine code.
- No hidden query behavior.
- No silent inference of business-critical fields.
- No silent ambiguity resolution.
- No destructive regeneration of config.

## Quality Standard
The practical target is not "magic perfect accuracy". The correct target is:

- deterministic where possible
- explicit where ambiguous
- measurable in production
- cheap enough to run at scale
- easy to extend without rewriting the engine

## Final Architecture Shape
- `DomainSpec` as the source of truth
- `generated/` config from the domain generator agent
- `manual/` overrides for curated corrections
- merged validated runtime config
- generic runtime services consuming only validated config

## Non-Goals
- zero literals of any kind in runtime code
- fully autonomous onboarding with no review
- using AI to generate executable SQL for normal deterministic requests
- silently “fixing” uncertain mappings at runtime

## Success Signals
- [ ] New domain onboarding requires no core planner changes.
- [ ] Domain generation works from `db_url` plus reverse-engineered schema input.
- [x] Core runtime now supports typed `DomainSpec` validation.
- [x] Core runtime now supports `generated/` plus `manual/` artifact loading.
- [ ] Runtime accuracy improves on replay benchmarks.
- [ ] Average LLM usage per request drops.
- [ ] Domain config remains readable and reviewable.
