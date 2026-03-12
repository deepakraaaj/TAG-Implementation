# TODOs: LLM Guardrails

## Phase 1: Contracts
- [ ] Add `guardrails/models.py` for `IntermediateFrame`, `EvidenceBundle`, `VerificationReport`, and `ValidationReport`.
- [ ] Add a compact session summary contract for `ChatService`.
- [ ] Define token budget defaults by route.

## Phase 2: Services
- [ ] Implement intermediate contract builder service.
- [ ] Implement evidence assembler for SQL results, manifests, and runtime state.
- [ ] Implement claim verifier service.
- [ ] Implement response validator service.

## Phase 3: Runtime Integration
- [ ] Call intermediate builder from `app/services/chat/service.py`.
- [ ] Insert verifier before final response generation on SQL flows.
- [ ] Insert validator before terminal result emission.
- [ ] Preserve existing stream/result contract.

## Phase 4: Metrics and Logging
- [ ] Add verifier and validator counters.
- [ ] Add estimated token-saved metric.
- [ ] Add failure reason logging without leaking secrets.

## Phase 5: Tests
- [ ] Unit test claim coverage logic.
- [ ] Unit test clarification and abstention rules.
- [ ] Unit test validator leak checks.
- [ ] Add regression tests for unsupported causal claims.
- [ ] Add regression tests for ambiguous prompts.

## Phase 6: Rollout
- [ ] Run observe-only mode first.
- [ ] Review blocked-response metrics.
- [ ] Enable enforcement on SQL route.
- [ ] Expand to chat route after regression review.
