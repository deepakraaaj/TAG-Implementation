# TAG Assistant Request Routing

Date: 2026-03-11
Purpose: Define slash-style request prefixes so the correct docs and workflow are used before implementation.

For copyable chat examples, also see `docs/dev/tag-assistant/request-guide.md`.

## How To Use This
Start your request with one of these prefixes:

- `/feature`
- `/bugfix`
- `/bug fix`
- `/review`
- `/investigate`
- `/docs`

These are routing conventions for this repo. They are not shell commands. They tell the agent which markdown files to read first and what execution workflow to follow.

## Prefix Map
### `/feature`
Use for:
- new features
- feature enhancements
- behavior changes

Read first:
1. `docs/product/tag-assistant/application-context.md`
2. `docs/product/tag-assistant/prd.md`
3. `docs/dev/tag-assistant/code-writing-patterns.md`
4. `docs/dev/tag-assistant/feature-change-workflow.md`
5. `.agents/SPECS/feature-change-evaluation.md`

If the feature touches LLM quality, safety, token usage, verification, or hallucination control, also read:
6. `docs/dev/llm-guardrails/SPEC.md`
7. `docs/dev/llm-guardrails/DESIGN.md`
8. `.agents/SPECS/llm-guardrails.md`

Execution expectation:
- build intermediate feature brief
- analyze impact on existing features
- recommend better approach if needed
- verify and validate plan
- implement with tests

### `/bugfix` or `/bug fix`
Use for:
- broken behavior
- regressions
- incorrect output
- failing tests caused by product bugs

Read first:
1. `docs/product/tag-assistant/application-context.md`
2. `README.md`
3. `docs/dev/tag-assistant/code-writing-patterns.md`
4. `docs/dev/tag-assistant/bugfix-workflow.md`
5. `.agents/SPECS/bugfix-evaluation.md`

If the bug touches LLM output quality or unsupported claims, also read:
6. `docs/dev/llm-guardrails/SPEC.md`
7. `.agents/SPECS/llm-guardrails.md`

Execution expectation:
- build intermediate bug brief
- reproduce or trace the issue
- identify root cause with evidence
- assess blast radius
- implement minimal safe fix
- add regression coverage

### `/review`
Use for:
- code review
- change review
- patch review

Read first:
1. `docs/product/tag-assistant/application-context.md`
2. `docs/dev/tag-assistant/code-writing-patterns.md`

Execution expectation:
- prioritize findings
- focus on bugs, regressions, risks, and missing tests
- do not default to implementation unless asked

### `/investigate`
Use for:
- root cause analysis
- unknown issue tracing
- architecture understanding before changes

Read first:
1. `docs/product/tag-assistant/application-context.md`
2. `README.md`
3. `docs/dev/tag-assistant/bugfix-workflow.md`

Execution expectation:
- investigate first
- gather evidence
- do not change code unless asked

### `/docs`
Use for:
- PRD updates
- architecture docs
- workflow docs
- API documentation

Read first:
1. `docs/product/tag-assistant/application-context.md`
2. `docs/README.md`
3. the target document family relevant to the request

Execution expectation:
- update docs consistently
- keep product, architecture, and dev docs aligned

## Default Rule
If no prefix is provided, infer the task type from the request. If a prefix is provided, it overrides inference and should be treated as the workflow selector for the turn.
