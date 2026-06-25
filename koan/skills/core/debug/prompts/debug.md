You are debugging a failed issue using a structured hypothesis-driven approach. A previous fix attempt failed — your job is to find the actual root cause and fix it correctly.

## Tracker Issue

**Issue**: {ISSUE_URL}
**Title**: {ISSUE_TITLE}

## Issue Content

{ISSUE_BODY}

## Previous Failure Context

{FAILURE_CONTEXT}

## Additional Context

{CONTEXT}
{PROJECT_MEMORY}
## Instructions — Structured Debug Loop

You MUST follow these four steps in order. Do not skip any step.

### Step 1 — Reproduce

1. **Read the issue and failure context carefully.** Understand what was attempted before and why it failed.
2. **Read the project's CLAUDE.md** (if it exists) for coding conventions.
3. **Write a minimal failing test** that demonstrates the bug. The test must fail before your fix. If the bug cannot be reproduced in a test (infrastructure, config, etc.), document why and write a characterization test that captures the current broken behavior.
4. **Run the test** and confirm it fails with the expected error.

### Step 2 — Hypothesize

5. **Explore the relevant code.** Use Read, Glob, and Grep to trace the execution path. Look at the code that the previous attempt changed — understand why those changes were wrong or insufficient.
6. **Form a hypothesis** about the actual root cause. Emit it as a marker on its own line:

   ```
   DEBUG_HYPOTHESIS: <your one-line root cause explanation>
   ```

   This marker is parsed by the system. Be specific — "race condition between cache write and read in SessionStore.get()" not "timing issue."

7. **Validate your hypothesis** against the reproduction test. If you can't explain why the test fails given your hypothesis, revise it and emit a new `DEBUG_HYPOTHESIS:` line.

### Step 3 — Minimal Fix

8. **Apply the narrowest change** that addresses your hypothesis. Fix only what the hypothesis identifies — no drive-by cleanups, no refactoring.

{BRANCH_SECTION}

{@include implementation-workflow}

### Step 4 — Verify

9. **Run the reproduction test** — it must now pass.
10. **Run the full relevant test suite** — no regressions.
11. If either fails, return to Step 2 with a revised hypothesis. Do NOT iterate more than 3 times — if the third hypothesis fails, commit what you have and document the remaining issue in the PR description.

## Rules

- **Hypothesis first.** Never write a fix without a `DEBUG_HYPOTHESIS:` line.
- **Minimal changes.** Fix only what the hypothesis identifies.
- **One commit per phase.** Each iteration through the loop is one commit.
- **Never commit to main.** Always work on the feature branch.
- **Test before commit.** Never commit code that breaks tests.
- **Always submit a PR.** The debug is not complete until a draft PR is created.
- **Use Koan's issue helper for tracker writes.** If you must fetch, create, or comment on tracker issues yourself, use `{KOAN_PYTHON} -m app.issue_cli` instead of direct `gh issue` commands so GitHub and Jira projects both work.
