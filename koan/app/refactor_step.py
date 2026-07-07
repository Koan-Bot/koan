"""
Kōan -- Reusable refactor pass.

Refactors the changed code on the current branch for simplicity, reuse, and
clarity while preserving behavior, makes one clean convention-aware commit,
optionally runs the test suite (with a single fix attempt), and optionally
pushes the new commit to the branch's remote.

This is the shared core used by:
  - the standalone ``/refactor`` skill runner (``app.refactor_pr``), which wraps
    it with PR checkout + a GitHub summary comment; and
  - the ``/implement``, ``/rebase`` and ``/fix`` pipelines, which run it as an
    internal quality pass immediately before the private review gate (no PR
    comment).

The pass never reaches outside the working tree on its own beyond a plain
``git push`` of the new commit — it adds a commit on top of existing work, so a
fast-forward push is always sufficient (no force, no rebase).
"""

import logging
from pathlib import Path
from typing import List, Optional, Tuple

logger = logging.getLogger(__name__)

# Skill dir used to load the refactor prompt when a caller doesn't supply one.
_REFACTOR_SKILL_DIR = (
    Path(__file__).resolve().parent.parent / "skills" / "core" / "refactor"
)

# Markers the prompt emits around its human-readable change summary.
_SUMMARY_START = "===REFACTOR_SUMMARY==="
_SUMMARY_END = "===END==="

# Commit message used when the prompt emits no COMMIT_SUBJECT line.
_DEFAULT_COMMIT_MSG = "refactor: simplify and clean up recent changes"


def _noop(_msg: str) -> None:
    """No-op notifier used when a caller passes no notify_fn."""
    return None


class RefactorResult:
    """Outcome of a single refactor pass."""

    __slots__ = ("bullets", "committed", "pushed", "summary", "tests", "tests_ok")

    def __init__(
        self,
        committed: bool = False,
        pushed: bool = False,
        bullets: Optional[List[str]] = None,
        tests: str = "",
        summary: str = "",
        tests_ok: bool = True,
    ):
        self.committed = committed
        self.pushed = pushed
        self.bullets = bullets or []
        self.tests = tests
        self.summary = summary
        # False only when tests ran and stayed broken after the fix attempt.
        # True when tests passed, were repaired, were skipped, or never ran.
        # Callers use this to gate pushing broken refactors.
        self.tests_ok = tests_ok

    def __bool__(self) -> bool:
        return self.committed


def run_refactor_pass(
    project_path: str,
    *,
    context: str = "",
    skill_dir: Optional[Path] = None,
    base_branch: Optional[str] = None,
    branch: Optional[str] = None,
    notify_fn=None,
    run_tests: bool = True,
    push: bool = True,
    project_name: str = "",
    instance_dir: str = "",
    preferred_remote: Optional[str] = None,
) -> RefactorResult:
    """Refactor the changed code on the current branch.

    Args:
        project_path: Local path to the project repository.
        context: Optional extra focus for the refactor (e.g. "focus on the
            tests"); flows into the prompt.
        skill_dir: Path to the refactor skill dir for prompt loading.
        base_branch: Branch the diff is computed against. Auto-resolved when
            omitted.
        branch: Branch being refactored. Defaults to the current branch.
        notify_fn: Progress notifier. Defaults to a no-op.
        run_tests: Run the project's tests after refactoring (single fix
            attempt on failure).
        push: Push the new commit to the branch's remote (plain fast-forward).
            The push is skipped when tests ran and stayed broken — a broken
            refactor is never published.
        project_name: Project name (auto-resolved from the path when omitted).
        instance_dir: Instance directory (reserved for future use).
        preferred_remote: Remote to push to first (e.g. the PR head remote for
            fork PRs). When given, it is tried before the default
            ``origin`` / ``upstream`` fallbacks.

    Returns:
        A :class:`RefactorResult`. ``committed`` is False when the refactor
        produced no changes (a clean no-op) or the Claude step failed.
    """
    from app.claude_step import _get_current_branch, run_claude_step
    from app.commit_conventions import get_project_commit_guidance
    from app.config import get_skill_max_turns, get_skill_timeout
    from app.projects_config import resolve_base_branch
    from app.prompts import load_prompt_or_skill
    from app.utils import project_name_for_path

    notify = notify_fn or _noop
    skill_dir = skill_dir or _REFACTOR_SKILL_DIR

    branch = branch or _get_current_branch(project_path)
    project_name = project_name or project_name_for_path(project_path)
    base_branch = base_branch or resolve_base_branch(project_name, project_path)

    commit_guidance = get_project_commit_guidance(project_path, base_branch)

    prompt = load_prompt_or_skill(
        skill_dir,
        "refactor",
        PROJECT_PATH=project_path,
        BASE_BRANCH=base_branch,
        BRANCH=branch,
        CONTEXT=context or "",
        COMMIT_GUIDANCE=commit_guidance,
    )

    actions_log: List[str] = []
    step = run_claude_step(
        prompt=prompt,
        project_path=project_path,
        commit_msg=_DEFAULT_COMMIT_MSG,
        success_label="Applied refactoring",
        failure_label="Refactor step failed",
        actions_log=actions_log,
        max_turns=get_skill_max_turns(),
        timeout=get_skill_timeout(),
        use_convention_subject=bool(commit_guidance),
    )

    if not step.committed:
        return RefactorResult(
            committed=False,
            summary="No refactoring changes were needed.",
        )

    bullets = _parse_summary_bullets(step.output)

    tests_status = ""
    tests_ok = True
    if run_tests:
        tests_status, tests_ok = _run_tests_with_one_fix(project_path, notify)

    # Never publish a refactor that leaves tests broken. Keep the commit
    # locally (the next checkout resets the branch to the remote tip) but
    # skip the push and surface the failure via tests_ok.
    do_push = push and tests_ok
    pushed = (
        _push_branch(branch, project_path, preferred_remote) if do_push else False
    )

    summary = "Refactoring applied"
    if bullets:
        summary += f" ({len(bullets)} change(s))"
    return RefactorResult(
        committed=True,
        pushed=pushed,
        bullets=bullets,
        tests=tests_status,
        tests_ok=tests_ok,
        summary=summary,
    )


def run_internal_refactor_pass(
    project_path: str,
    *,
    project_name: str = "",
    instance_dir: str = "",
    base_branch: Optional[str] = None,
    notify_fn=None,
) -> RefactorResult:
    """Best-effort internal refactor pass for /implement, /rebase, /fix.

    Runs immediately before the private review gate. Produces an extra commit
    and pushes it, but never posts a PR comment (internal workflow) and never
    raises — a refactor failure must not block the review gate.

    Opt out via ``refactor_pass.enabled: false`` in config.yaml (default on).
    """
    from app.config import is_internal_refactor_pass_enabled

    notify = notify_fn or _noop

    if not is_internal_refactor_pass_enabled():
        return RefactorResult(
            committed=False, summary="refactor pass disabled by config",
        )

    try:
        result = run_refactor_pass(
            project_path,
            context="",
            base_branch=base_branch,
            notify_fn=notify,
            run_tests=True,
            push=True,
            project_name=project_name,
            instance_dir=instance_dir,
        )
    except Exception as e:  # noqa: BLE001 — best-effort, must not block the gate
        logger.warning("[refactor] internal pass failed: %s", e)
        return RefactorResult(committed=False, summary=f"refactor pass skipped: {e}")

    if result.committed and result.pushed:
        notify("🛠️ Refactor pass applied before review gate")
    elif result.committed and not result.tests_ok:
        # Commit kept locally but not pushed — tests stayed broken. The review
        # gate must not be told this "applied"; surface it as a distinct warning.
        notify(
            "⚠️ Refactor pass kept locally — tests still failing, not pushed "
            f"({result.tests})"
        )
    else:
        notify("🛠️ Refactor pass: no changes needed")
    return result


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _parse_summary_bullets(output: str) -> List[str]:
    """Extract bullet lines from the ===REFACTOR_SUMMARY===…===END=== block."""
    if not output:
        return []
    start = output.find(_SUMMARY_START)
    if start == -1:
        return []
    start += len(_SUMMARY_START)
    end = output.find(_SUMMARY_END, start)
    block = output[start:] if end == -1 else output[start:end]

    bullets: List[str] = []
    for line in block.splitlines():
        stripped = line.strip()
        if stripped.startswith(("-", "*")):
            text = stripped[1:].strip()
            if text:
                bullets.append(text)
    return bullets


def _run_tests_with_one_fix(project_path: str, notify) -> Tuple[str, bool]:
    """Run the test suite; on failure, attempt one Claude fix and re-run.

    Returns a ``(status, tests_ok)`` tuple: *status* is a short human-readable
    string, *tests_ok* is False only when tests ran and stayed broken after the
    fix attempt (callers gate the push on this).
    """
    from app.claude_step import run_claude_step, run_project_tests
    from app.config import get_skill_max_turns
    from app.pr_review import detect_test_command
    from app.prompts import load_prompt

    test_cmd = detect_test_command(project_path)
    if not test_cmd:
        return "skipped (no test command detected)", True

    notify("Running tests after refactor...")
    result = run_project_tests(project_path, test_cmd=test_cmd)
    if result.get("passed"):
        return f"passing ({result.get('details', 'OK')})", True

    notify("Tests failing after refactor — attempting one fix...")
    fix_prompt = load_prompt(
        "refactor_test_fix",
        PROJECT_PATH=project_path,
        TEST_CMD=test_cmd,
        TEST_OUTPUT=result.get("output", "")[:3000],
    )
    fix_actions: List[str] = []
    fix_step = run_claude_step(
        prompt=fix_prompt,
        project_path=project_path,
        commit_msg="fix: repair tests after refactoring",
        success_label="Repaired tests after refactor",
        failure_label="Test-repair step failed",
        actions_log=fix_actions,
        max_turns=get_skill_max_turns(),
        timeout=600,
    )

    retest = run_project_tests(project_path, test_cmd=test_cmd)
    if retest.get("passed"):
        return "fixed and passing", True
    detail = retest.get("details", "unknown")
    if getattr(fix_step, "committed", False):
        # The repair committed something but tests are still red — fold that
        # into the status so the caller (and PR comment) reports it honestly.
        return f"still failing ({detail}; repair commit kept locally)", False
    return f"still failing ({detail})", False


def _push_branch(
    branch: str, project_path: str, preferred_remote: Optional[str] = None,
) -> bool:
    """Plain (non-force) push of *branch*, trying each remote in order.

    *preferred_remote* (e.g. the PR head remote for fork PRs) is tried first so
    the new commit lands on the branch's actual remote rather than ``origin``.

    Returns True on the first successful push, False if all remotes reject it.
    Never raises — a push failure is reported to the caller, not fatal.
    """
    from app.claude_step import _run_git
    from app.git_utils import ordered_remotes

    for remote in ordered_remotes(preferred_remote, cwd=project_path):
        try:
            _run_git(["git", "push", remote, branch], cwd=project_path)
            return True
        except Exception as e:  # noqa: BLE001 — try the next remote
            logger.warning("[refactor] push to %s failed: %s", remote, e)
            continue
    return False
