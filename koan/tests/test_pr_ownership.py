"""Tests for PR ownership checks in rebase, ci_check, and check_runner.

When a PR was opened by another koan instance (different branch prefix),
the skills should refuse to operate on it.
"""

import importlib.util
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from app.skills import SkillContext


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load_handler(skill_name):
    """Load a skill handler module by name."""
    path = Path(__file__).parent.parent / "skills" / "core" / skill_name / "handler.py"
    spec = importlib.util.spec_from_file_location(f"{skill_name}_handler", str(path))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def ctx(tmp_path):
    """Create a basic SkillContext for tests."""
    instance_dir = tmp_path / "instance"
    instance_dir.mkdir()
    missions_md = instance_dir / "missions.md"
    missions_md.write_text("## Pending\n\n## In Progress\n\n## Done\n")
    return SkillContext(
        koan_root=tmp_path,
        instance_dir=instance_dir,
        command_name="test",
        args="",
        send_message=MagicMock(),
    )


def _project_patches():
    """Common patches for project resolution."""
    return [
        patch("app.utils.resolve_project_path", return_value="/home/koan"),
        patch("app.utils.get_known_projects", return_value=[("koan", "/home/koan")]),
    ]


# ---------------------------------------------------------------------------
# is_own_pr helper
# ---------------------------------------------------------------------------

class TestIsOwnPr:
    def test_own_branch_returns_true(self):
        from app.github_skill_helpers import is_own_pr

        gh_response = json.dumps({"headRefName": "koan/fix-stuff"})
        with patch("app.github.run_gh", return_value=gh_response), \
             patch("app.config.get_branch_prefix", return_value="koan/"):
            owned, branch = is_own_pr("owner", "repo", "42")
            assert owned is True
            assert branch == "koan/fix-stuff"

    def test_foreign_branch_returns_false(self):
        from app.github_skill_helpers import is_own_pr

        gh_response = json.dumps({"headRefName": "other-bot/feature"})
        with patch("app.github.run_gh", return_value=gh_response), \
             patch("app.config.get_branch_prefix", return_value="koan/"):
            owned, branch = is_own_pr("owner", "repo", "42")
            assert owned is False
            assert branch == "other-bot/feature"

    def test_empty_head_returns_false(self):
        from app.github_skill_helpers import is_own_pr

        gh_response = json.dumps({})
        with patch("app.github.run_gh", return_value=gh_response), \
             patch("app.config.get_branch_prefix", return_value="koan/"):
            owned, branch = is_own_pr("owner", "repo", "42")
            assert owned is False
            assert branch == ""


# ---------------------------------------------------------------------------
# /ci_check -- ownership
# ---------------------------------------------------------------------------

class TestCiCheckOwnership:
    @pytest.fixture
    def handler(self):
        return _load_handler("ci_check")

    def test_rejects_pr_from_other_instance(self, handler, ctx):
        ctx.args = "https://github.com/sukria/koan/pull/55"
        with _project_patches()[0], _project_patches()[1], \
             patch("app.github_skill_helpers.is_own_pr",
                   return_value=(False, "other-instance/branch")), \
             patch("app.utils.insert_pending_mission") as mock_insert:
            result = handler.handle(ctx)
            assert "Not my PR" in result
            assert "other-instance/branch" in result
            mock_insert.assert_not_called()

    def test_accepts_pr_from_own_instance(self, handler, ctx):
        ctx.args = "https://github.com/sukria/koan/pull/55"
        with _project_patches()[0], _project_patches()[1], \
             patch("app.github_skill_helpers.is_own_pr",
                   return_value=(True, "koan/fix-ci")), \
             patch("app.utils.insert_pending_mission") as mock_insert:
            result = handler.handle(ctx)
            assert "queued" in result.lower()
            mock_insert.assert_called_once()

    def test_ownership_check_failure_returns_error(self, handler, ctx):
        ctx.args = "https://github.com/sukria/koan/pull/55"
        with _project_patches()[0], _project_patches()[1], \
             patch("app.github_skill_helpers.is_own_pr",
                   side_effect=Exception("gh not found")):
            result = handler.handle(ctx)
            assert "\u274c" in result
            assert "ownership" in result.lower()


# ---------------------------------------------------------------------------
# /rebase -- ownership
# ---------------------------------------------------------------------------

class TestRebaseOwnership:
    @pytest.fixture
    def handler(self):
        return _load_handler("rebase")

    def test_rejects_pr_from_other_instance(self, handler, ctx):
        ctx.args = "https://github.com/sukria/koan/pull/55"
        with _project_patches()[0], _project_patches()[1], \
             patch("app.github_skill_helpers.is_own_pr",
                   return_value=(False, "other-bot/feature")), \
             patch("app.utils.insert_pending_mission") as mock_insert:
            result = handler.handle(ctx)
            assert "Not my PR" in result
            assert "other-bot/feature" in result
            mock_insert.assert_not_called()

    def test_accepts_pr_from_own_instance(self, handler, ctx):
        ctx.args = "https://github.com/sukria/koan/pull/55"
        with _project_patches()[0], _project_patches()[1], \
             patch("app.github_skill_helpers.is_own_pr",
                   return_value=(True, "koan/my-branch")), \
             patch("app.utils.insert_pending_mission") as mock_insert:
            result = handler.handle(ctx)
            assert "queued" in result.lower()
            mock_insert.assert_called_once()

    def test_ownership_check_failure_returns_error(self, handler, ctx):
        ctx.args = "https://github.com/sukria/koan/pull/55"
        with _project_patches()[0], _project_patches()[1], \
             patch("app.github_skill_helpers.is_own_pr",
                   side_effect=Exception("network error")):
            result = handler.handle(ctx)
            assert "\u274c" in result
            assert "ownership" in result.lower()


# ---------------------------------------------------------------------------
# check_runner -- ownership guard on auto-queued rebase
# ---------------------------------------------------------------------------

class TestCheckRunnerOwnership:
    def _make_pr_data(self, head_branch="koan/my-branch", mergeable="CONFLICTING",
                      review_decision=None, is_draft=False):
        return {
            "state": "OPEN",
            "mergeable": mergeable,
            "reviewDecision": review_decision,
            "updatedAt": "2026-01-01T00:00:00Z",
            "headRefName": head_branch,
            "baseRefName": "main",
            "title": "Test PR",
            "isDraft": is_draft,
            "author": {"login": "bot"},
            "url": "https://github.com/owner/repo/pull/1",
        }

    def test_foreign_pr_needing_rebase_is_skipped(self, tmp_path):
        """A foreign PR that needs rebase should be skipped and mark_checked called."""
        from app.check_runner import _handle_pr

        instance_dir = tmp_path / "instance"
        instance_dir.mkdir()
        (instance_dir / "missions.md").write_text("## Pending\n\n## In Progress\n\n## Done\n")

        pr_data = self._make_pr_data(head_branch="other-bot/fix", mergeable="CONFLICTING")
        notify = MagicMock()

        with patch("app.check_runner._fetch_pr_metadata", return_value=pr_data), \
             patch("app.check_tracker.has_changed", return_value=True), \
             patch("app.check_tracker.mark_checked") as mock_mark, \
             patch("app.config.get_branch_prefix", return_value="koan/"), \
             patch("app.utils.insert_pending_mission") as mock_insert:
            success, msg = _handle_pr("owner", "repo", "1", instance_dir, str(tmp_path), notify)

            assert success is True
            assert "not mine" in msg.lower()
            assert "other-bot/fix" in msg
            mock_insert.assert_not_called()
            # mark_checked must be called to prevent repeated notifications
            mock_mark.assert_called_once()

    def test_foreign_pr_needing_review_is_skipped(self, tmp_path):
        """A foreign PR that needs review should be skipped and mark_checked called."""
        from app.check_runner import _handle_pr

        instance_dir = tmp_path / "instance"
        instance_dir.mkdir()
        (instance_dir / "missions.md").write_text("## Pending\n\n## In Progress\n\n## Done\n")

        pr_data = self._make_pr_data(
            head_branch="other-bot/fix", mergeable="MERGEABLE", review_decision=None,
        )
        notify = MagicMock()

        with patch("app.check_runner._fetch_pr_metadata", return_value=pr_data), \
             patch("app.check_tracker.has_changed", return_value=True), \
             patch("app.check_tracker.mark_checked") as mock_mark, \
             patch("app.config.get_branch_prefix", return_value="koan/"), \
             patch("app.utils.insert_pending_mission") as mock_insert:
            success, msg = _handle_pr("owner", "repo", "1", instance_dir, str(tmp_path), notify)

            assert success is True
            assert "not mine" in msg.lower()
            assert "other-bot/fix" in msg
            mock_insert.assert_not_called()
            mock_mark.assert_called_once()

    def test_own_pr_needing_rebase_is_queued(self, tmp_path):
        """An own PR that needs rebase should queue a rebase mission."""
        from app.check_runner import _handle_pr

        instance_dir = tmp_path / "instance"
        instance_dir.mkdir()
        (instance_dir / "missions.md").write_text("## Pending\n\n## In Progress\n\n## Done\n")

        pr_data = self._make_pr_data(head_branch="koan/my-fix", mergeable="CONFLICTING")
        notify = MagicMock()

        with patch("app.check_runner._fetch_pr_metadata", return_value=pr_data), \
             patch("app.check_tracker.has_changed", return_value=True), \
             patch("app.check_tracker.mark_checked"), \
             patch("app.config.get_branch_prefix", return_value="koan/"), \
             patch("app.utils.resolve_project_path", return_value="/home/koan"), \
             patch("app.utils.get_known_projects", return_value=[("koan", "/home/koan")]), \
             patch("app.utils.insert_pending_mission") as mock_insert:
            success, msg = _handle_pr("owner", "repo", "1", instance_dir, str(tmp_path), notify)

            assert success is True
            assert "Rebase queued" in msg
            mock_insert.assert_called_once()

    def test_own_pr_needing_review_is_queued(self, tmp_path):
        """An own PR that needs review should queue a review mission."""
        from app.check_runner import _handle_pr

        instance_dir = tmp_path / "instance"
        instance_dir.mkdir()
        (instance_dir / "missions.md").write_text("## Pending\n\n## In Progress\n\n## Done\n")

        pr_data = self._make_pr_data(
            head_branch="koan/my-fix", mergeable="MERGEABLE", review_decision=None,
        )
        notify = MagicMock()

        with patch("app.check_runner._fetch_pr_metadata", return_value=pr_data), \
             patch("app.check_tracker.has_changed", return_value=True), \
             patch("app.check_tracker.mark_checked"), \
             patch("app.config.get_branch_prefix", return_value="koan/"), \
             patch("app.utils.resolve_project_path", return_value="/home/koan"), \
             patch("app.utils.get_known_projects", return_value=[("koan", "/home/koan")]), \
             patch("app.utils.insert_pending_mission") as mock_insert:
            success, msg = _handle_pr("owner", "repo", "1", instance_dir, str(tmp_path), notify)

            assert success is True
            assert "review queued" in msg.lower()
            mock_insert.assert_called_once()
