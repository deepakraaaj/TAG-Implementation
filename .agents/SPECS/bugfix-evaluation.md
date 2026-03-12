# Agent Spec: Bugfix Evaluation

## Purpose
Use this spec whenever the user asks to fix a bug, regression, incorrect behavior, or failing runtime path.

## Read Order
1. `docs/product/tag-assistant/application-context.md`
2. `README.md`
3. `docs/dev/tag-assistant/code-writing-patterns.md`
4. `docs/dev/tag-assistant/bugfix-workflow.md`

If the issue touches LLM quality or unsupported claims, also read:

5. `docs/dev/llm-guardrails/SPEC.md`
6. `.agents/SPECS/llm-guardrails.md`

## Mandatory Workflow
1. Build an intermediate bug brief.
2. Identify the runtime path and likely owner modules.
3. Verify the bug with evidence.
4. Confirm root cause.
5. Validate the fix plan.
6. Implement the smallest safe fix.
7. Add regression coverage.

## Intermediate Bug Brief
Use this shape:

```yaml
bug_summary:
expected_behavior:
actual_behavior:
reproduction:
runtime_path:
impacted_modules:
existing_features_at_risk:
root_cause_hypothesis:
test_plan:
```

## Verifier Checklist
- Is the failure reproduced or otherwise evidenced?
- Is the runtime path correct?
- Do the named files actually own the bug?
- Could the fix affect stream contracts, SQL safety, flows, cache, or manifests?

## Validator Checklist
- Is the fix minimal?
- Does it preserve existing intended behavior?
- Does it avoid bypassing safety gates?
- Does it include regression coverage?

## Working Rule
Do not jump straight to a patch for bugfix work. First verify the failure and root cause from repo evidence.
