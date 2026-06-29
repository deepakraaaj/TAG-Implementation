# TAG Assistant Bugfix Workflow

Date: 2026-03-11
Purpose: Standard workflow for bugfix requests so fixes are grounded in actual runtime behavior and minimize regressions.

## Goal
When a bugfix request arrives, the system should:

- read the relevant app and code-writing docs first
- reproduce or trace the failure before proposing a fix
- identify the actual owning runtime path
- verify root cause with evidence
- validate that the fix is minimal and safe
- add regression protection

## Required Read Order For Bugfix Requests
1. `docs/product/tag-assistant/application-context.md`
2. `docs/project/README.md`
3. `docs/dev/tag-assistant/code-writing-patterns.md`
4. `docs/dev/tag-assistant/request-routing.md`
5. `docs/dev/tag-assistant/bugfix-workflow.md`

If the bug touches LLM output quality, unsupported claims, or token behavior, also read:

6. `docs/dev/llm-guardrails/SPEC.md`

Only after that, inspect the specific code and tests related to the bug.

## Step 1: Intermediate Bug Brief
Before changing code, build a compact brief with:

- bug summary
- expected behavior
- actual behavior
- reproduction input or failing test
- runtime path
- likely impacted modules
- existing features at risk
- suspected constraints
- verification plan

## Step 2: Verify The Failure
Confirm the bug is real through repo evidence:

- failing test
- reproducible request path
- code trace
- log or error evidence
- clear mismatch between expected and actual behavior

Do not guess root cause before the failure is verified.

## Step 3: Root Cause Analysis
Answer:

- where does the incorrect behavior originate?
- which module actually owns the behavior?
- is this a local defect or a contract problem?
- can the fix cause regressions in stream behavior, SQL safety, flow continuation, cache, or manifests?

## Step 4: Validator For Fix Plan
Before implementing, validate that the fix plan is:

- minimal
- aligned with the current architecture
- not bypassing existing safety rules
- not hiding a broader refactor inside a bugfix
- paired with regression coverage

## Step 5: Implementation
Prefer:

- the smallest safe fix
- preserving current contracts
- adding or updating tests near the failure
- documenting behavior if the bug exposed a missing rule

Avoid:

- rewriting unrelated code
- bypassing validators
- changing output shape without checking consumers

## Blast Radius Checklist
Every bugfix should consider:

- endpoint contracts
- graph edges and route behavior
- session and cache state
- SQL validation and authorization
- domain config assumptions
- metrics or audit events
- nearby tests

## Token-Saving Rules For Bugfix Work
- start from docs, not broad repo exploration
- inspect only files on the failing runtime path
- use the intermediate brief instead of repeating context

## Anti-Hallucination Rules For Bugfix Work
- do not assume the first suspected file is the owner
- do not claim root cause without repo evidence
- do not call a fix safe until blast radius is checked
- if the issue is not reproduced, state that clearly

## Standard Deliverable For Bugfixes
Before or alongside implementation, include:

- bug summary
- runtime path
- root cause
- impacted files
- regression risk
- fix approach
- verification summary
- validation summary
- test plan
