# Agent Spec: Feature Change Evaluation

## Purpose
Use this spec whenever the user asks to add, modify, or review a feature in this repository.

## Read Order
1. `docs/product/tag-assistant/application-context.md`
2. `docs/product/tag-assistant/prd.md`
3. `docs/dev/tag-assistant/code-writing-patterns.md`
4. `docs/dev/tag-assistant/feature-change-workflow.md`

If the request touches LLM behavior, also read:

5. `docs/dev/llm-guardrails/SPEC.md`
6. `.agents/SPECS/llm-guardrails.md`

## Mandatory Workflow
1. Build an intermediate feature brief.
2. Identify the runtime path and impacted modules.
3. Analyze possible regressions to existing features.
4. Recommend a better approach if the requested implementation is weak.
5. Verify the plan against repo evidence.
6. Validate the plan before coding.
7. Implement only after the plan passes verification and validation.

## Intermediate Feature Brief
Use this compact shape:

```yaml
feature_goal:
runtime_path:
impacted_modules:
existing_features_at_risk:
constraints:
unknowns:
recommended_approach:
test_plan:
```

## Verifier Checklist
- Is the runtime path correct?
- Do the named files actually own the behavior?
- Could this affect stream contracts, SQL safety, flows, cache, or manifests?
- Is the better approach aligned with the current architecture?

## Validator Checklist
- Are there unsupported assumptions?
- Is the plan minimal and testable?
- Does it preserve existing behavior unless change is intended?
- Does it include regression coverage?

## Working Rule
Do not jump straight into code for feature work. First prove that the plan fits the current repo.
