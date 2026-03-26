"""Tests for the /abort core skill -- abort current in-progress mission."""

from pathlib import Path
from unittest.mock import patch

import pytest

from app.skills import SkillContext


class TestAbortHandler:
    """Test the abort skill handler directly."""

    def _make_ctx(self, tmp_path, args=""):
        instance_dir = tmp_path / "instance"
        instance_dir.mkdir(exist_ok=True)
        return SkillContext(
            koan_root=tmp_path,
            instance_dir=instance_dir,
            command_name="abort",
            args=args,
        )

    def test_creates_abort_signal_file(self, tmp_path):
        from skills.core.abort.handler import handle

        ctx = self._make_ctx(tmp_path)
        result = handle(ctx)

        from app.signals import ABORT_FILE
        abort_path = tmp_path / ABORT_FILE
        assert abort_path.exists()
        assert abort_path.read_text() == "abort"
        assert "Abort requested" in result

    def test_returns_confirmation_message(self, tmp_path):
        from skills.core.abort.handler import handle

        ctx = self._make_ctx(tmp_path)
        result = handle(ctx)

        assert isinstance(result, str)
        assert "abort" in result.lower() or "Abort" in result


class TestAbortSignalConstant:
    """Test the ABORT_FILE constant in signals module."""

    def test_abort_file_constant_exists(self):
        from app.signals import ABORT_FILE
        assert ABORT_FILE == ".koan-abort"


class TestAbortSkillRegistration:
    """Test that the abort skill is properly registered."""

    def test_abort_skill_discovered(self):
        from app.skills import build_registry

        registry = build_registry()
        skill = registry.find_by_command("abort")
        assert skill is not None
        assert skill.name == "abort"

    def test_abort_skill_has_group(self):
        from app.skills import build_registry

        registry = build_registry()
        skill = registry.find_by_command("abort")
        assert skill is not None
        assert skill.group == "missions"


class TestAbortInRunPy:
    """Test abort-related behavior in run.py globals and guards."""

    def test_abort_flag_blocks_retry(self):
        """_maybe_retry_mission should skip retry when mission was aborted."""
        import app.run as run_mod

        # Set the abort flag
        run_mod._last_mission_aborted = True
        try:
            result = run_mod._maybe_retry_mission(
                claude_exit=1,
                stdout_file="/dev/null",
                stderr_file="/dev/null",
                cmd=["echo"],
                project_path="/tmp",
                pre_head="abc123",
                instance="/tmp/instance",
                project_name="test",
                run_num=1,
                has_mission=True,
            )
            # Should return early without retrying
            assert result == (1, "/dev/null", "/dev/null")
        finally:
            run_mod._last_mission_aborted = False

    def test_timeout_flag_still_blocks_retry(self):
        """Existing timeout guard should still work alongside abort guard."""
        import app.run as run_mod

        run_mod._last_mission_timed_out = True
        try:
            result = run_mod._maybe_retry_mission(
                claude_exit=1,
                stdout_file="/dev/null",
                stderr_file="/dev/null",
                cmd=["echo"],
                project_path="/tmp",
                pre_head="abc123",
                instance="/tmp/instance",
                project_name="test",
                run_num=1,
                has_mission=True,
            )
            assert result == (1, "/dev/null", "/dev/null")
        finally:
            run_mod._last_mission_timed_out = False
