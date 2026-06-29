# TAG Assistant Feature Change Workflow

Date: 2026-03-11
Purpose: Standard workflow for new feature requests so implementation starts from documented context, evaluates impact on existing features, and recommends better approaches when needed.

## Goal
When a new feature request arrives, the system should:

- read the existing application and architecture docs first
- understand how the code has been written
- identify affected runtime paths and neighboring features
- recommend a better approach if the requested approach is risky or too expensive
- use an intermediate/verifier/validator workflow to reduce hallucination and token waste

## Required Read Order For New Feature Requests
1. `docs/product/tag-assistant/application-context.md`
2. `docs/product/tag-assistant/prd.md`
3. `docs/project/README.md`
4. `docs/dev/tag-assistant/code-writing-patterns.md`
5. `docs/dev/llm-guardrails/SPEC.md` if the request touches LLM behavior, quality, token usage, or safety

Only after that, inspect the specific code files needed for the feature.

## Step 1: Intermediate Feature Brief
Before writing code, produce a compact internal brief with:

- feature request summary
- product goal being served
- runtime path: chat, SQL, report, flow, or cross-cutting
- likely impacted modules
- likely impacted existing features
- safety concerns
- open unknowns
- recommended implementation shape
- verification plan

This brief exists to avoid repeatedly re-reading large parts of the repository and to keep the task grounded in actual repo context.

## Step 2: Impact Analysis
Every feature request must answer:

- which existing features may be affected?
- which contracts might change?
- which tests are at risk?
- which services or nodes are tightly coupled to this behavior?
- does the request fit the current architecture, or is there a cleaner extension point?

Minimum areas to check:

- endpoint request/response contracts
- graph path changes
- session and cache behavior
- SQL safety rules
- domain manifest assumptions
- metrics and audit side effects
- flow continuation behavior

## Step 3: Better-Approach Recommendation
If the requested implementation is not the best fit, recommend a better approach before coding.

Examples:

- prefer manifest/config-driven logic over hardcoded domain branches
- prefer a focused service over enlarging `ChatService` unnecessarily
- prefer deterministic checks before extra LLM calls
- prefer contract-preserving changes over breaking output shape

The recommendation must explain:

- why the original approach is risky or expensive
- why the alternative better fits this repo
- what tradeoff the alternative introduces

## Step 4: Verifier
Before implementation begins, verify the plan against repo evidence.

Verifier checks:
- the affected runtime path is correctly identified
- the proposed files and modules actually own that behavior
- existing safety gates remain in place
- the change does not silently break stream contracts or SQL safety
- the improvement is consistent with product direction

If verification is weak, gather more evidence or narrow the proposal.

## Step 5: Validator
Before committing to code, validate the plan.

Validator checks:
- no unsupported assumptions are treated as facts
- no large unrelated refactor is hidden inside a feature request
- the implementation shape is minimal and testable
- the change preserves existing contracts unless the request explicitly changes them
- the proposal includes a regression test strategy

If validation fails, revise the plan before coding.

## Token-Saving Rules For Feature Work
- start from context docs, not broad repo exploration
- inspect only the active runtime path and adjacent files
- keep the intermediate brief compact
- reuse existing docs/specs instead of regenerating explanations
- avoid repeating unchanged architecture context in every step

## Anti-Hallucination Rules For Feature Work
- do not claim a file owns behavior until the repo shows it
- do not assume a change is isolated without checking adjacent contracts
- do not recommend architecture changes without mapping them to current modules
- do not state “safe” unless the verifier checks passed
- when the impact is unclear, say so and investigate before coding

## Standard Deliverable For New Features
Before or alongside implementation, the response should include:

- affected runtime path
- impacted modules/files
- possible regressions
- recommended approach
- why that approach is better
- verification summary
- validation summary
- test plan

## Success Condition
A new feature should be implemented with less re-discovery, lower token usage, clearer impact analysis, and fewer LLM-style mistakes about how the current application actually works.
