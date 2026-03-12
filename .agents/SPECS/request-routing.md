# Agent Spec: Request Routing

## Purpose
Use slash-style request prefixes in this repo to select the correct docs and workflow before implementation.

## Supported Prefixes
- `/feature`
- `/bugfix`
- `/bug fix`
- `/review`
- `/investigate`
- `/docs`

## Routing Rules
### `/feature`
Read:
1. `docs/product/tag-assistant/application-context.md`
2. `docs/product/tag-assistant/prd.md`
3. `docs/dev/tag-assistant/code-writing-patterns.md`
4. `docs/dev/tag-assistant/feature-change-workflow.md`
5. `.agents/SPECS/feature-change-evaluation.md`

### `/bugfix` or `/bug fix`
Read:
1. `docs/product/tag-assistant/application-context.md`
2. `README.md`
3. `docs/dev/tag-assistant/code-writing-patterns.md`
4. `docs/dev/tag-assistant/bugfix-workflow.md`
5. `.agents/SPECS/bugfix-evaluation.md`

### `/review`
Read:
1. `docs/product/tag-assistant/application-context.md`
2. `docs/dev/tag-assistant/code-writing-patterns.md`

### `/investigate`
Read:
1. `docs/product/tag-assistant/application-context.md`
2. `README.md`
3. `docs/dev/tag-assistant/bugfix-workflow.md`

### `/docs`
Read:
1. `docs/product/tag-assistant/application-context.md`
2. `docs/README.md`

## Working Rule
If a supported prefix appears at the beginning of the user request, treat it as the workflow selector for that turn.
