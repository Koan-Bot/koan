"""
Koan -- Prompt audit runner.

Audits system and skill prompts for quality, clarity, redundancy,
staleness, and effectiveness. Uses signal data from post-mission hooks
to correlate prompt usage with outcomes.

Pipeline:
1. Discover all system-prompts/*.md and skills/*/prompts/*.md files
2. Compute metrics (lines, words, sections, placeholders)
3. Read signal data from prompt-audit-signals.jsonl (last 7 days)
4. Invoke Claude with structured audit prompt
5. Write findings to shared-journal.md
6. Extract actionable findings summary

CLI:
    python3 -m skills.core.prompt_audit.prompt_audit_runner \
        --project-name <name> --instance-dir <dir>
"""

import json
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, NamedTuple, Optional, Tuple

from app.prompts import load_skill_prompt


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

class PromptMetrics(NamedTuple):
    """Computed metrics for a single prompt file."""
    path: str
    lines: int
    words: int
    sections: int
    placeholders: List[str]


class AuditFinding(NamedTuple):
    """A single finding from the prompt audit."""
    prompt_path: str
    category: str
    severity: str
    summary: str
    suggestion: str


# ---------------------------------------------------------------------------
# Prompt discovery
# ---------------------------------------------------------------------------

def discover_prompts(koan_root: Path) -> List[Path]:
    """Find all prompt .md files in system-prompts/ and skills/*/prompts/.

    Excludes the prompt-audit prompt itself to avoid self-referential auditing.
    """
    prompts = []

    # System prompts
    sys_prompts_dir = koan_root / "koan" / "system-prompts"
    if sys_prompts_dir.is_dir():
        for p in sorted(sys_prompts_dir.glob("*.md")):
            prompts.append(p)

    # Skill prompts (core and any other scopes)
    skills_dir = koan_root / "koan" / "skills"
    if skills_dir.is_dir():
        for prompt_file in sorted(skills_dir.glob("**/prompts/*.md")):
            prompts.append(prompt_file)

    # Exclude the audit prompt itself to prevent recursion
    prompts = [
        p for p in prompts
        if p.name != "prompt_audit.md"
        or "prompt_audit" not in str(p.parent.parent)
    ]

    return prompts


# ---------------------------------------------------------------------------
# Metrics computation
# ---------------------------------------------------------------------------

_SECTION_RE = re.compile(r"^#{1,4}\s+", re.MULTILINE)
_PLACEHOLDER_RE = re.compile(r"\{([A-Z][A-Z0-9_]*)\}")


def compute_metrics(prompt_path: Path) -> PromptMetrics:
    """Compute quality metrics for a single prompt file."""
    try:
        text = prompt_path.read_text(encoding="utf-8")
    except OSError:
        return PromptMetrics(
            path=str(prompt_path), lines=0, words=0,
            sections=0, placeholders=[],
        )

    lines = text.count("\n") + (1 if text and not text.endswith("\n") else 0)
    words = len(text.split())
    sections = len(_SECTION_RE.findall(text))
    placeholders = sorted(set(_PLACEHOLDER_RE.findall(text)))

    return PromptMetrics(
        path=str(prompt_path),
        lines=lines,
        words=words,
        sections=sections,
        placeholders=placeholders,
    )


def compute_all_metrics(prompts: List[Path]) -> List[PromptMetrics]:
    """Compute metrics for all discovered prompts."""
    return [compute_metrics(p) for p in prompts]


# ---------------------------------------------------------------------------
# Signal data reading
# ---------------------------------------------------------------------------

def read_signals(
    instance_dir: Path,
    days: int = 7,
) -> List[dict]:
    """Read prompt audit signal data from the last N days.

    Signal data is written by the optional post_mission hook
    (prompt_audit_signals.py) to instance/prompt-audit-signals.jsonl.
    Each line is a JSON object with at least: timestamp, prompt_file,
    exit_code, duration_minutes.
    """
    signals_path = instance_dir / "prompt-audit-signals.jsonl"
    if not signals_path.exists():
        return []

    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    signals = []

    try:
        for line in signals_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue

            # Filter by timestamp if present
            ts_str = entry.get("timestamp", "")
            if ts_str:
                try:
                    ts = datetime.fromisoformat(ts_str)
                    if ts.tzinfo is None:
                        ts = ts.replace(tzinfo=timezone.utc)
                    if ts < cutoff:
                        continue
                except (ValueError, TypeError):
                    pass  # Keep entries with unparseable timestamps

            signals.append(entry)
    except OSError:
        return []

    return signals


# ---------------------------------------------------------------------------
# Prompt building
# ---------------------------------------------------------------------------

def _format_metrics_table(metrics_list: List[PromptMetrics]) -> str:
    """Format prompt metrics as a markdown table for the audit prompt."""
    lines = ["| Prompt | Lines | Words | Sections | Placeholders |"]
    lines.append("|--------|-------|-------|----------|-------------|")
    for m in metrics_list:
        # Show relative path from koan root for brevity
        short_path = m.path
        for prefix in ("koan/system-prompts/", "koan/skills/"):
            idx = short_path.find(prefix)
            if idx >= 0:
                short_path = short_path[idx:]
                break
        placeholders = ", ".join(m.placeholders) if m.placeholders else "—"
        lines.append(
            f"| `{short_path}` | {m.lines} | {m.words} | "
            f"{m.sections} | {placeholders} |"
        )
    return "\n".join(lines)


def _format_signals_summary(signals: List[dict]) -> str:
    """Summarize signal data for the audit prompt."""
    if not signals:
        return "No signal data available. The prompt audit signals hook is not active or has no recent data."

    total = len(signals)
    successes = sum(1 for s in signals if s.get("exit_code", 1) == 0)
    failures = total - successes

    # Per-prompt breakdown
    by_prompt: Dict[str, dict] = {}
    for s in signals:
        prompt = s.get("prompt_file", "unknown")
        stats = by_prompt.setdefault(prompt, {"total": 0, "ok": 0, "fail": 0})
        stats["total"] += 1
        if s.get("exit_code", 1) == 0:
            stats["ok"] += 1
        else:
            stats["fail"] += 1

    lines = [
        f"**Signal data** (last 7 days): {total} missions tracked, "
        f"{successes} succeeded, {failures} failed.",
        "",
    ]

    if by_prompt:
        lines.append("| Prompt | Runs | OK | Failed |")
        lines.append("|--------|------|----|--------|")
        for prompt, stats in sorted(by_prompt.items()):
            lines.append(
                f"| `{prompt}` | {stats['total']} | "
                f"{stats['ok']} | {stats['fail']} |"
            )

    return "\n".join(lines)


def build_audit_prompt(
    metrics_list: List[PromptMetrics],
    signals: List[dict],
    skill_dir: Optional[Path] = None,
) -> str:
    """Build the full audit prompt with metrics and signal data."""
    metrics_table = _format_metrics_table(metrics_list)
    signals_summary = _format_signals_summary(signals)

    return load_skill_prompt(
        skill_dir or Path(__file__).resolve().parent,
        "prompt_audit",
        METRICS_TABLE=metrics_table,
        SIGNALS_SUMMARY=signals_summary,
    )


# ---------------------------------------------------------------------------
# Finding parser
# ---------------------------------------------------------------------------

_FIELD_RE = re.compile(
    r"^(PROMPT|CATEGORY|SEVERITY|SUMMARY|SUGGESTION):\s*(.+)",
    re.MULTILINE,
)


def parse_findings(raw_output: str) -> List[AuditFinding]:
    """Parse ---FINDING--- blocks from Claude's audit output."""
    blocks = re.split(r"---FINDING---", raw_output)
    findings = []

    for block in blocks:
        block = block.strip()
        if not block:
            continue

        fields: Dict[str, str] = {}
        for match in _FIELD_RE.finditer(block):
            field = match.group(1).lower()
            value = match.group(2).strip()
            fields[field] = value

        if fields.get("prompt") and fields.get("summary"):
            findings.append(AuditFinding(
                prompt_path=fields.get("prompt", ""),
                category=fields.get("category", "quality"),
                severity=fields.get("severity", "medium"),
                summary=fields.get("summary", ""),
                suggestion=fields.get("suggestion", ""),
            ))

    return findings


# ---------------------------------------------------------------------------
# Report writing
# ---------------------------------------------------------------------------

def write_report(
    instance_dir: Path,
    findings: List[AuditFinding],
    metrics_list: List[PromptMetrics],
) -> Path:
    """Write the audit report to shared-journal.md."""
    report_path = instance_dir / "memory" / "prompt-audit-report.md"
    report_path.parent.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines = [
        f"# Prompt Audit Report",
        f"",
        f"*Generated: {timestamp}*",
        f"",
        f"## Summary",
        f"",
        f"- **Prompts audited:** {len(metrics_list)}",
        f"- **Findings:** {len(findings)}",
        f"",
    ]

    if findings:
        lines.append("## Findings")
        lines.append("")
        for i, f in enumerate(findings, 1):
            severity_icon = {
                "critical": "\U0001f534",
                "high": "\U0001f7e0",
                "medium": "\U0001f7e1",
                "low": "\U0001f7e2",
            }.get(f.severity, "\u2753")
            lines.append(
                f"{i}. {severity_icon} **[{f.severity}]** `{f.prompt_path}` "
                f"({f.category})"
            )
            lines.append(f"   {f.summary}")
            if f.suggestion:
                lines.append(f"   *Suggestion:* {f.suggestion}")
            lines.append("")
    else:
        lines.append("No actionable findings. All prompts look good!")
        lines.append("")

    report_path.write_text("\n".join(lines), encoding="utf-8")
    return report_path


# ---------------------------------------------------------------------------
# Claude CLI integration
# ---------------------------------------------------------------------------

def _run_claude_audit(prompt: str, project_path: str) -> str:
    """Run Claude CLI with read-only tools and return the output text."""
    from app.cli_provider import run_command_streaming
    from app.config import get_skill_timeout

    return run_command_streaming(
        prompt, project_path,
        allowed_tools=["Read", "Glob", "Grep"],
        max_turns=20,
        timeout=get_skill_timeout(),
    )


# ---------------------------------------------------------------------------
# Main runner
# ---------------------------------------------------------------------------

def run_prompt_audit(
    project_name: str,
    instance_dir: str,
    koan_root: str,
    notify_fn=None,
    skill_dir: Optional[Path] = None,
) -> Tuple[bool, str]:
    """Execute a prompt audit.

    Args:
        project_name: Project name for labeling.
        instance_dir: Path to instance directory.
        koan_root: Path to the koan repository root.
        notify_fn: Optional callback for progress notifications.
        skill_dir: Optional path to the skill directory for prompts.

    Returns:
        (success, summary) tuple.
    """
    if notify_fn is None:
        from app.notify import send_telegram
        notify_fn = send_telegram

    instance_path = Path(instance_dir)
    koan_path = Path(koan_root)

    # Step 1: Discover prompts
    notify_fn("\U0001f4dd Auditing prompts...")
    prompts = discover_prompts(koan_path)
    if not prompts:
        return True, "No prompt files found to audit."

    notify_fn(f"\U0001f50d Found {len(prompts)} prompt file(s). Computing metrics...")

    # Step 2: Compute metrics
    metrics_list = compute_all_metrics(prompts)

    # Step 3: Read signal data
    signals = read_signals(instance_path)

    # Step 4: Build and run audit
    prompt = build_audit_prompt(metrics_list, signals, skill_dir=skill_dir)

    try:
        raw_output = _run_claude_audit(prompt, koan_root)
    except RuntimeError as e:
        return False, f"Prompt audit failed: {e}"

    if not raw_output:
        return False, "Prompt audit produced no output."

    # Step 5: Parse findings
    findings = parse_findings(raw_output)

    # Step 6: Write report
    report_path = write_report(instance_path, findings, metrics_list)

    # Build summary
    if findings:
        summary = (
            f"Prompt audit complete: {len(prompts)} prompts audited, "
            f"{len(findings)} finding(s). Report: {report_path.name}"
        )
    else:
        summary = (
            f"Prompt audit complete: {len(prompts)} prompts audited, "
            f"no issues found."
        )
    notify_fn(f"\u2705 {summary}")

    return True, summary


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main(argv=None):
    """CLI entry point for prompt_audit_runner."""
    import argparse

    parser = argparse.ArgumentParser(
        description="Audit system and skill prompts for quality issues."
    )
    parser.add_argument(
        "--project-name", required=True,
        help="Project name for labeling",
    )
    parser.add_argument(
        "--instance-dir", required=True,
        help="Path to instance directory",
    )
    parser.add_argument(
        "--koan-root", default=None,
        help="Path to koan repository root (defaults to KOAN_ROOT env)",
    )
    cli_args = parser.parse_args(argv)

    import os
    koan_root = cli_args.koan_root or os.environ.get("KOAN_ROOT", "")
    if not koan_root:
        print("Error: --koan-root or KOAN_ROOT env var required.", file=sys.stderr)
        return 1

    skill_dir = Path(__file__).resolve().parent

    success, summary = run_prompt_audit(
        project_name=cli_args.project_name,
        instance_dir=cli_args.instance_dir,
        koan_root=koan_root,
        skill_dir=skill_dir,
    )
    print(summary)
    return 0 if success else 1


if __name__ == "__main__":
    sys.exit(main())
