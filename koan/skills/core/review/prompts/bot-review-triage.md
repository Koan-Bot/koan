You are triaging inline code review comments left by automated review bots on a pull request.

For each bot comment below, classify it as:
- **actionable**: A genuine code finding (bug, style issue, performance concern, security risk) that the PR author should consider.
- **noise**: Meta-information (deployment previews, coverage reports, summary blocks, auto-generated tables) with no code-level suggestion.

## Diff context

{diff}

## Bot comments to triage

{bot_comments}

## Output format

Return a JSON array. Each element:
```json
{
  "comment_id": <integer — the GitHub comment ID>,
  "classification": "actionable" | "noise",
  "reply": "<string — the reply to post>"
}
```

Rules for the `reply` field:
- For **actionable** comments: Start with "Acknowledged: " followed by a 1-2 sentence response addressing the finding (agree, disagree with reason, or note it's already handled).
- For **noise** comments: Return an empty string (no reply will be posted).
- Keep replies concise and constructive.

Only include entries where `classification` is `"actionable"`. Omit noise entries entirely.
