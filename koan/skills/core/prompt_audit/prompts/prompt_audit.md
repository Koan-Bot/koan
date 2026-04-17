You are auditing the prompt files used by the Koan autonomous agent system. Your goal is to evaluate each prompt for quality, clarity, redundancy, staleness, and effectiveness — then produce structured findings.

## Prompt Inventory

The following prompts were discovered, along with their computed metrics:

{METRICS_TABLE}

## Signal Data

{SIGNALS_SUMMARY}

## Instructions

### Phase 1 — Read Each Prompt

Read every prompt file listed in the inventory above. For each one, evaluate:

1. **Clarity**: Is the intent immediately obvious? Are instructions unambiguous?
2. **Redundancy**: Are there repeated instructions within the prompt or across prompts?
3. **Staleness**: Does the prompt reference features, patterns, or conventions that no longer exist in the codebase?
4. **Effectiveness**: Based on signal data (if available), are prompts correlated with higher failure rates?
5. **Length efficiency**: Is the prompt concise, or does it use excessive words to convey simple ideas? Could sections be cut without losing meaning?
6. **Structure**: Are sections well-organized? Do headings help navigation? Are examples clear?
7. **Placeholder hygiene**: Are all placeholders (`{NAME}`) documented and used consistently?

### Phase 2 — Cross-Prompt Analysis

Look across all prompts for:
- **Contradictions**: Do any prompts give conflicting instructions?
- **Duplication**: Are the same instructions repeated in multiple prompts?
- **Gaps**: Are there missing prompts for important workflows?
- **Consistency**: Do prompts use consistent terminology and formatting?

### Phase 3 — Produce Findings

For EACH finding, produce a block in this exact format. Use `---FINDING---` as separator between findings:

```
---FINDING---
PROMPT: <relative path to the prompt file>
CATEGORY: <clarity|redundancy|staleness|effectiveness|length|structure|placeholder|contradiction|gap>
SEVERITY: <critical|high|medium|low>
SUMMARY: <1-2 sentences describing the issue>
SUGGESTION: <Concrete, actionable suggestion for improvement>
```

### Severity Guide

- **critical**: Prompt gives incorrect or dangerous instructions that could cause harm
- **high**: Prompt is confusing enough to cause frequent misinterpretation or has stale references
- **medium**: Prompt could be improved for clarity or conciseness
- **low**: Minor style or organization improvement

## Rules

- **Read-only.** Do not modify any files. This is a pure analysis task.
- **Be specific.** Reference exact sections, lines, or phrases in your findings.
- **Be actionable.** Each finding must have a concrete suggestion, not just "improve this".
- **Quality over quantity.** Report only findings that would meaningfully improve prompt quality.
- **Do NOT audit this prompt.** Skip the prompt-audit prompt itself to avoid self-referential recursion.
- **Use the exact separator format** (`---FINDING---`) so findings can be parsed programmatically.
