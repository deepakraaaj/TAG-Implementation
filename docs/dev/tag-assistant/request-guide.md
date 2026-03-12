# Request Guide

Use one of these:

```text
/feature Goal | Why | Scope | Do not break | Success
/bugfix Goal | Expected | Actual | Scope | Do not break
/review Focus on bugs, regressions, missing tests
/investigate Problem | No code changes yet
/docs What to update
```

## Copy-Paste

```text
/feature Add export filters | Users need CSV by assignee/date | Reports only | Do not break /chat stream | Tests pass
```

```text
/bugfix Chat ends without terminal result | Expected final result event | Actual spinner hangs | Streaming path | Do not break token/error order
```

```text
/review Check this branch for SQL validation and streaming regressions
```

```text
/investigate Why are summary prompts routed to SQL? No code changes yet.
```

## If you want better output, add one of these

```text
impact analysis first
check what else this affects
recommend a better approach
minimal safe fix
add regression tests
```
