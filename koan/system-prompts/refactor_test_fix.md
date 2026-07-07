# Refactor Test Repair

The test suite is failing after a refactoring pass. Fix the failures by
correcting the refactoring — **preserve the original behavior**. Only modify
what is necessary to make the tests pass again; do not add features, weaken the
tests, or touch unrelated code.

Working directory: `{PROJECT_PATH}`

Test command: `{TEST_CMD}`

Failing test output:

```
{TEST_OUTPUT}
```

## Process

1. Reproduce the failure with the test command above.
2. Identify the minimal change that restores the original behavior broken by
   the refactor.
3. Apply the fix with the smallest reasonable diff.
4. Re-run the tests to confirm they pass.

Make your edits with the Read/Edit/Write tools only. **Do not run
`git add`, `git commit`, `git push`, or switch branches** — the surrounding
automation stages and commits your fix for you.
