# Repository Instructions

## Startup Context
For any new request in this repository, read these files first before exploring the codebase:

1. `docs/product/tag-assistant/application-context.md`
2. `docs/product/tag-assistant/prd.md`
3. `docs/project/README.md`
4. `docs/dev/tag-assistant/request-routing.md`

These files are the canonical fast-start context for understanding the application. Do not begin by rediscovering the repository from scratch unless those documents are missing or insufficient for the current task.

## Request Prefix Routing
If the user starts a request with a supported prefix, treat it as the task selector for the turn.

Supported prefixes:
- `/feature`
- `/bugfix`
- `/bug fix`
- `/review`
- `/investigate`
- `/docs`

When a prefix is present, follow the document routing defined in:

1. `docs/dev/tag-assistant/request-routing.md`
2. `.agents/SPECS/request-routing.md`

## Task-Specific Context
If the request is to add, modify, or review a feature, also read:

1. `docs/dev/tag-assistant/code-writing-patterns.md`
2. `docs/dev/tag-assistant/feature-change-workflow.md`
3. `.agents/SPECS/feature-change-evaluation.md`

If the request is to fix a bug or regression, also read:

1. `docs/dev/tag-assistant/code-writing-patterns.md`
2. `docs/dev/tag-assistant/bugfix-workflow.md`
3. `.agents/SPECS/bugfix-evaluation.md`

If the request touches LLM quality, token optimization, hallucination reduction, verification, or validation, also read:

1. `docs/dev/llm-guardrails/SPEC.md`
2. `docs/dev/llm-guardrails/DESIGN.md`
3. `.agents/SPECS/llm-guardrails.md`

If the request is about overall app behavior or product direction, also read:

1. `.agents/SPECS/tag-assistant.md`

## Working Rules
- Treat `docs/product/tag-assistant/application-context.md` as the shortest reliable overview of the app.
- Use the PRD to understand product goal, solved areas, gaps, and roadmap before proposing major changes.
- If the user provides a supported slash prefix, use that routing instead of generic task inference.
- For feature work, produce an impact-aware plan before coding: runtime path, affected files, regressions, better approach, and test strategy.
- For bugfix work, verify failure and root cause before patching.
- Inspect code only after you know which runtime path the request belongs to.
- Preserve the existing stream contract and SQL safety behavior unless the request explicitly changes them.
