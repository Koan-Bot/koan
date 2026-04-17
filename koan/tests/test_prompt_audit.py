"""Tests for the /prompt_audit skill — handler, runner, and parser."""

import importlib.util
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from app.skills import SkillContext


# ---------------------------------------------------------------------------
# Handler tests
# ---------------------------------------------------------------------------

HANDLER_PATH = (
    Path(__file__).parent.parent / "skills" / "core" / "prompt_audit" / "handler.py"
)


def _load_handler():
    """Load the prompt_audit handler module dynamically."""
    spec = importlib.util.spec_from_file_location("prompt_audit_handler", str(HANDLER_PATH))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def handler():
    return _load_handler()


@pytest.fixture
def ctx(tmp_path):
    """Create a basic SkillContext for tests."""
    instance_dir = tmp_path / "instance"
    instance_dir.mkdir()
    missions_path = instance_dir / "missions.md"
    missions_path.write_text("# Missions\n\n## Pending\n\n## In Progress\n\n## Done\n")
    return SkillContext(
        koan_root=tmp_path,
        instance_dir=instance_dir,
        command_name="prompt_audit",
        args="",
        send_message=MagicMock(),
    )


class TestHandlerRouting:
    def test_help_flag_returns_usage(self, handler, ctx):
        ctx.args = "--help"
        result = handler.handle(ctx)
        assert "Usage:" in result

    def test_help_short_flag_returns_usage(self, handler, ctx):
        ctx.args = "-h"
        result = handler.handle(ctx)
        assert "Usage:" in result

    def test_no_args_defaults_to_koan(self, handler, ctx):
        """No args should default to koan project, not error."""
        with patch("app.utils.resolve_project_path", return_value="/path/koan"):
            with patch("app.utils.insert_pending_mission"):
                result = handler.handle(ctx)
        assert "queued" in result.lower()


class TestHandlerQueueMission:
    @patch("app.utils.resolve_project_path", return_value="/path/koan")
    @patch("app.utils.insert_pending_mission")
    def test_named_project(self, mock_insert, mock_resolve, handler, ctx):
        ctx.args = "koan"
        result = handler.handle(ctx)

        assert "queued" in result.lower()
        assert "koan" in result
        mock_insert.assert_called_once()
        mission_entry = mock_insert.call_args[0][1]
        assert "[project:koan]" in mission_entry
        assert "/prompt_audit" in mission_entry

    @patch("app.utils.resolve_project_path", return_value=None)
    def test_unknown_project(self, mock_resolve, handler, ctx):
        ctx.args = "nonexistent"
        with patch("app.utils.get_known_projects", return_value=[("koan", "/p")]):
            result = handler.handle(ctx)
        assert "\u274c" in result
        assert "nonexistent" in result


# ---------------------------------------------------------------------------
# Runner tests — prompt discovery
# ---------------------------------------------------------------------------

from skills.core.prompt_audit.prompt_audit_runner import (
    AuditFinding,
    PromptMetrics,
    compute_metrics,
    discover_prompts,
    parse_findings,
    read_signals,
    write_report,
    _format_metrics_table,
    _format_signals_summary,
)


class TestDiscoverPrompts:
    def test_discovers_system_prompts(self, tmp_path):
        sys_dir = tmp_path / "koan" / "system-prompts"
        sys_dir.mkdir(parents=True)
        (sys_dir / "agent.md").write_text("# Agent prompt")
        (sys_dir / "chat.md").write_text("# Chat prompt")

        prompts = discover_prompts(tmp_path)
        assert len(prompts) == 2
        names = [p.name for p in prompts]
        assert "agent.md" in names
        assert "chat.md" in names

    def test_discovers_skill_prompts(self, tmp_path):
        skill_dir = tmp_path / "koan" / "skills" / "core" / "audit" / "prompts"
        skill_dir.mkdir(parents=True)
        (skill_dir / "audit.md").write_text("# Audit prompt")

        prompts = discover_prompts(tmp_path)
        assert len(prompts) == 1
        assert prompts[0].name == "audit.md"

    def test_excludes_prompt_audit_itself(self, tmp_path):
        skill_dir = tmp_path / "koan" / "skills" / "core" / "prompt_audit" / "prompts"
        skill_dir.mkdir(parents=True)
        (skill_dir / "prompt_audit.md").write_text("# Self-referential")

        prompts = discover_prompts(tmp_path)
        assert len(prompts) == 0

    def test_empty_dir(self, tmp_path):
        prompts = discover_prompts(tmp_path)
        assert prompts == []


# ---------------------------------------------------------------------------
# Runner tests — metrics
# ---------------------------------------------------------------------------


class TestComputeMetrics:
    def test_basic_metrics(self, tmp_path):
        prompt = tmp_path / "test.md"
        prompt.write_text(
            "# Title\n\nSome words here.\n\n## Section Two\n\nMore content with {PROJECT_NAME}.\n"
        )
        m = compute_metrics(prompt)
        assert m.lines > 0
        assert m.words > 0
        assert m.sections == 2
        assert "PROJECT_NAME" in m.placeholders

    def test_no_placeholders(self, tmp_path):
        prompt = tmp_path / "simple.md"
        prompt.write_text("Just plain text.\n")
        m = compute_metrics(prompt)
        assert m.placeholders == []

    def test_nonexistent_file(self, tmp_path):
        m = compute_metrics(tmp_path / "missing.md")
        assert m.lines == 0
        assert m.words == 0

    def test_multiple_placeholders(self, tmp_path):
        prompt = tmp_path / "multi.md"
        prompt.write_text("Hello {FOO} and {BAR} and {FOO} again.\n")
        m = compute_metrics(prompt)
        assert m.placeholders == ["BAR", "FOO"]  # sorted, deduplicated


# ---------------------------------------------------------------------------
# Runner tests — signal reading
# ---------------------------------------------------------------------------


class TestReadSignals:
    def test_reads_valid_signals(self, tmp_path):
        signals_path = tmp_path / "prompt-audit-signals.jsonl"
        now = datetime.now(timezone.utc)
        entry = {
            "timestamp": now.isoformat(),
            "project_name": "koan",
            "exit_code": 0,
            "duration_minutes": 5,
        }
        signals_path.write_text(json.dumps(entry) + "\n")

        result = read_signals(tmp_path, days=7)
        assert len(result) == 1
        assert result[0]["project_name"] == "koan"

    def test_filters_old_signals(self, tmp_path):
        signals_path = tmp_path / "prompt-audit-signals.jsonl"
        old = datetime.now(timezone.utc) - timedelta(days=30)
        entry = {
            "timestamp": old.isoformat(),
            "exit_code": 1,
        }
        signals_path.write_text(json.dumps(entry) + "\n")

        result = read_signals(tmp_path, days=7)
        assert len(result) == 0

    def test_missing_file(self, tmp_path):
        result = read_signals(tmp_path)
        assert result == []

    def test_malformed_json_skipped(self, tmp_path):
        signals_path = tmp_path / "prompt-audit-signals.jsonl"
        now = datetime.now(timezone.utc)
        signals_path.write_text(
            "not json\n"
            + json.dumps({"timestamp": now.isoformat(), "exit_code": 0}) + "\n"
        )
        result = read_signals(tmp_path, days=7)
        assert len(result) == 1


# ---------------------------------------------------------------------------
# Runner tests — finding parser
# ---------------------------------------------------------------------------


class TestParseFindings:
    def test_parses_single_finding(self):
        raw = (
            "---FINDING---\n"
            "PROMPT: koan/system-prompts/agent.md\n"
            "CATEGORY: clarity\n"
            "SEVERITY: medium\n"
            "SUMMARY: The agent prompt has ambiguous mode instructions.\n"
            "SUGGESTION: Rewrite the mode section with concrete examples.\n"
        )
        findings = parse_findings(raw)
        assert len(findings) == 1
        f = findings[0]
        assert f.prompt_path == "koan/system-prompts/agent.md"
        assert f.category == "clarity"
        assert f.severity == "medium"
        assert "ambiguous" in f.summary
        assert "Rewrite" in f.suggestion

    def test_parses_multiple_findings(self):
        raw = (
            "---FINDING---\n"
            "PROMPT: a.md\n"
            "CATEGORY: redundancy\n"
            "SEVERITY: low\n"
            "SUMMARY: Duplicated instructions.\n"
            "SUGGESTION: Remove the duplicate.\n"
            "---FINDING---\n"
            "PROMPT: b.md\n"
            "CATEGORY: staleness\n"
            "SEVERITY: high\n"
            "SUMMARY: References removed feature.\n"
            "SUGGESTION: Update to current API.\n"
        )
        findings = parse_findings(raw)
        assert len(findings) == 2
        assert findings[0].prompt_path == "a.md"
        assert findings[1].prompt_path == "b.md"

    def test_skips_incomplete_findings(self):
        raw = (
            "---FINDING---\n"
            "CATEGORY: clarity\n"
            "SEVERITY: low\n"
            # Missing PROMPT and SUMMARY — should be skipped
        )
        findings = parse_findings(raw)
        assert len(findings) == 0

    def test_empty_output(self):
        findings = parse_findings("")
        assert findings == []

    def test_no_findings_in_output(self):
        raw = "The prompts all look great! No issues found."
        findings = parse_findings(raw)
        assert findings == []


# ---------------------------------------------------------------------------
# Runner tests — report writing
# ---------------------------------------------------------------------------


class TestWriteReport:
    def test_writes_report_with_findings(self, tmp_path):
        findings = [
            AuditFinding(
                prompt_path="agent.md",
                category="clarity",
                severity="medium",
                summary="Ambiguous instructions.",
                suggestion="Be more specific.",
            ),
        ]
        metrics = [
            PromptMetrics(path="agent.md", lines=50, words=300, sections=5, placeholders=[]),
        ]
        report_path = write_report(tmp_path, findings, metrics)

        assert report_path.exists()
        content = report_path.read_text()
        assert "Prompt Audit Report" in content
        assert "Ambiguous instructions" in content
        assert "1" in content  # finding count
        assert "agent.md" in content

    def test_writes_report_no_findings(self, tmp_path):
        metrics = [
            PromptMetrics(path="agent.md", lines=50, words=300, sections=5, placeholders=[]),
        ]
        report_path = write_report(tmp_path, [], metrics)

        assert report_path.exists()
        content = report_path.read_text()
        assert "No actionable findings" in content


# ---------------------------------------------------------------------------
# Runner tests — formatting helpers
# ---------------------------------------------------------------------------


class TestFormatMetricsTable:
    def test_formats_table(self):
        metrics = [
            PromptMetrics(
                path="/root/koan/system-prompts/agent.md",
                lines=50, words=300, sections=5,
                placeholders=["PROJECT_NAME"],
            ),
        ]
        table = _format_metrics_table(metrics)
        assert "| Prompt |" in table
        assert "agent.md" in table
        assert "PROJECT_NAME" in table
        assert "50" in table


class TestFormatSignalsSummary:
    def test_no_signals(self):
        result = _format_signals_summary([])
        assert "No signal data" in result

    def test_with_signals(self):
        signals = [
            {"prompt_file": "agent.md", "exit_code": 0},
            {"prompt_file": "agent.md", "exit_code": 1},
            {"prompt_file": "chat.md", "exit_code": 0},
        ]
        result = _format_signals_summary(signals)
        assert "3 missions tracked" in result
        assert "2 succeeded" in result
        assert "1 failed" in result


# ---------------------------------------------------------------------------
# Runner tests — full pipeline (mocked Claude)
# ---------------------------------------------------------------------------


class TestRunPromptAudit:
    @patch("skills.core.prompt_audit.prompt_audit_runner._run_claude_audit")
    def test_full_pipeline_with_findings(self, mock_claude, tmp_path):
        from skills.core.prompt_audit.prompt_audit_runner import run_prompt_audit

        # Set up koan root with a prompt file
        sys_dir = tmp_path / "koan" / "system-prompts"
        sys_dir.mkdir(parents=True)
        (sys_dir / "agent.md").write_text("# Agent\n\nDo things with {PROJECT_NAME}.\n")

        instance_dir = tmp_path / "instance"
        instance_dir.mkdir()

        mock_claude.return_value = (
            "---FINDING---\n"
            "PROMPT: koan/system-prompts/agent.md\n"
            "CATEGORY: clarity\n"
            "SEVERITY: medium\n"
            "SUMMARY: Vague instructions.\n"
            "SUGGESTION: Add examples.\n"
        )

        notify = MagicMock()
        success, summary = run_prompt_audit(
            project_name="koan",
            instance_dir=str(instance_dir),
            koan_root=str(tmp_path),
            notify_fn=notify,
        )

        assert success
        assert "1 finding" in summary
        assert notify.call_count >= 2  # at least start + end notification

    @patch("skills.core.prompt_audit.prompt_audit_runner._run_claude_audit")
    def test_full_pipeline_no_findings(self, mock_claude, tmp_path):
        from skills.core.prompt_audit.prompt_audit_runner import run_prompt_audit

        sys_dir = tmp_path / "koan" / "system-prompts"
        sys_dir.mkdir(parents=True)
        (sys_dir / "agent.md").write_text("# Agent\n")

        instance_dir = tmp_path / "instance"
        instance_dir.mkdir()

        mock_claude.return_value = "All prompts look great. No issues found."

        notify = MagicMock()
        success, summary = run_prompt_audit(
            project_name="koan",
            instance_dir=str(instance_dir),
            koan_root=str(tmp_path),
            notify_fn=notify,
        )

        assert success
        assert "no issues" in summary.lower()

    def test_no_prompts_found(self, tmp_path):
        from skills.core.prompt_audit.prompt_audit_runner import run_prompt_audit

        instance_dir = tmp_path / "instance"
        instance_dir.mkdir()

        notify = MagicMock()
        success, summary = run_prompt_audit(
            project_name="koan",
            instance_dir=str(instance_dir),
            koan_root=str(tmp_path),
            notify_fn=notify,
        )

        assert success
        assert "No prompt files" in summary

    @patch("skills.core.prompt_audit.prompt_audit_runner._run_claude_audit")
    def test_claude_failure(self, mock_claude, tmp_path):
        from skills.core.prompt_audit.prompt_audit_runner import run_prompt_audit

        sys_dir = tmp_path / "koan" / "system-prompts"
        sys_dir.mkdir(parents=True)
        (sys_dir / "agent.md").write_text("# Agent\n")

        instance_dir = tmp_path / "instance"
        instance_dir.mkdir()

        mock_claude.side_effect = RuntimeError("CLI timeout")

        notify = MagicMock()
        success, summary = run_prompt_audit(
            project_name="koan",
            instance_dir=str(instance_dir),
            koan_root=str(tmp_path),
            notify_fn=notify,
        )

        assert not success
        assert "failed" in summary.lower()


# ---------------------------------------------------------------------------
# Signal hook example validation
# ---------------------------------------------------------------------------


def _load_example_hook():
    """Load the .py.example hook by copying to a .py temp file."""
    import shutil
    import tempfile

    example_path = (
        Path(__file__).parent.parent.parent
        / "instance.example" / "hooks" / "prompt_audit_signals.py.example"
    )
    assert example_path.exists(), f"Hook example not found at {example_path}"

    # Copy to a temp .py file so importlib can load it
    tmp_dir = tempfile.mkdtemp()
    tmp_file = Path(tmp_dir) / "prompt_audit_signals.py"
    shutil.copy2(example_path, tmp_file)

    spec = importlib.util.spec_from_file_location(
        "test_hook_example", str(tmp_file)
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class TestSignalHookExample:
    def test_hook_example_has_valid_structure(self):
        """Verify the example hook file defines the expected HOOKS dict."""
        mod = _load_example_hook()

        assert hasattr(mod, "HOOKS"), "Hook example must define a HOOKS dict"
        assert "post_mission" in mod.HOOKS
        assert callable(mod.HOOKS["post_mission"])

    def test_hook_writes_signal_data(self, tmp_path):
        """Verify the hook writes valid JSONL."""
        mod = _load_example_hook()

        ctx = {
            "instance_dir": str(tmp_path),
            "project_name": "testproj",
            "mission_title": "test mission",
            "exit_code": 0,
            "duration_minutes": 3.5,
        }
        mod.HOOKS["post_mission"](ctx)

        signals_path = tmp_path / "prompt-audit-signals.jsonl"
        assert signals_path.exists()

        data = json.loads(signals_path.read_text().strip())
        assert data["project_name"] == "testproj"
        assert data["exit_code"] == 0
        assert "timestamp" in data
