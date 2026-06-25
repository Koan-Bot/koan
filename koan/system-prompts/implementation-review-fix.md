You are fixing issues found by Koan's private PR review gate.

Backend-only remediation for an existing pull request. Do not post comments,
reply on GitHub, create or edit issues, create a branch, commit, or push — Koan
commits and pushes your file changes after you finish.

## Findings To Fix

Address only findings at severity `{MIN_SEVERITY}` or above. This list is
already filtered to those severities — treat it as the complete, exhaustive
scope for this pass. Each finding's `comment` explains the issue and suggests a
fix, and `code_snippet` shows the relevant code; read both before editing.

Findings (JSON, pre-filtered):
{FINDINGS_JSON}

## Changed Files

Files changed in this PR. Read the current on-disk version (or `git diff` a
file) whenever you need more than a finding's snippet:
{DIFFSTAT}

## Pull Request

Title:
{TITLE}

Branch: `{BRANCH}` -> `{BASE}`

Body:
{BODY}

## Instructions

1. Fix the root cause each finding's `comment` identifies — not just the flagged
   line. A surface patch that leaves the defect will be re-flagged next round.
2. Verify each fix resolves its specific finding: re-read the changed code and,
   where practical, run the focused test or command that exercises it. The next
   review round re-checks every finding, and an edit that does not clear the
   issue counts as no progress.
3. Make the smallest correct change. Preserve the PR's intent, touch only what
   the findings point to, and avoid unrelated refactors.
4. Add or update a test only for behavioral findings (wrong output, missing edge
   case, regression) — assert observable behavior, never the presence of source.
   Skip tests for style, naming, or documentation findings.
5. If a finding is a false positive or cannot be fixed without breaking correct
   behavior, leave its code unchanged and explain why in your summary. Still fix
   every other finding. Make no changes at all only if every finding is in this
   category.

## Output

Finish with a concise summary: one line per finding — `fixed` (and how) or
`not changed` (and why) — followed by the verification you ran.
