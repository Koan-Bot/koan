"""CI queue drain logic and CLI runner for /ci_check fix missions.

Two entry points:

1. ``drain_one(instance_dir)`` — called from iteration_manager between
   recurring injection and mission picking.  Makes a single non-blocking
   ``gh run list`` call.  If CI passed → remove entry.  If CI failed →
   inject a ``/ci_check <url>`` mission.  If still pending → skip.

2. ``main()`` — CLI entry point for the /ci_check fix mission.  Runs the
   blocking CI check-and-fix loop for a single PR (reusing the existing
   ``_run_ci_check_and_fix`` from ``rebase_pr.py``).

CLI usage::

    python3 -m app.ci_queue_runner <pr-url> --project-path <path>
"""

import json
import sys
from pathlib import Path
from typing import Optional, Tuple

from app.github import run_gh


def check_ci_status(
    branch: str,
    full_repo: str,
) -> Tuple[str, Optional[int]]:
    """Non-blocking CI status check via ``gh run list``.

    Returns:
        (status, run_id) where status is one of:
        "success", "failure", "pending", or "none".
    """
    try:
        raw = run_gh(
            "run", "list",
            "--branch", branch,
            "--repo", full_repo,
            "--json", "databaseId,status,conclusion",
            "--limit", "1",
        )
        runs = json.loads(raw) if raw.strip() else []
    except Exception as e:
        print(f"[ci_queue] CI status check error: {e}", file=sys.stderr)
        return ("pending", None)

    if not runs:
        return ("none", None)

    run = runs[0]
    run_id = run.get("databaseId")
    status = run.get("status", "").lower()
    conclusion = run.get("conclusion", "").lower()

    if status == "completed":
        if conclusion == "success":
            return ("success", run_id)
        return ("failure", run_id)

    # Still running (queued, in_progress, etc.)
    return ("pending", run_id)


def drain_one(instance_dir: str) -> Optional[str]:
    """Check the oldest CI queue entry and take action.

    Returns a short status string for logging, or None if the queue is empty.

    Actions per status:
    - success: remove from queue, log
    - failure: remove from queue, inject /ci_check mission
    - pending: leave in queue (will be checked next iteration)
    - none: remove from queue (no CI configured)
    - stale (>24h): remove from queue with warning
    """
    from app.ci_queue import peek, remove, remove_stale

    # Garbage-collect stale entries first
    stale = remove_stale(instance_dir)
    for entry in stale:
        pr_url = entry.get("pr_url", "?")
        print(
            f"[ci_queue] Removed stale entry (>24h): {pr_url}",
            file=sys.stderr,
        )

    entry = peek(instance_dir)
    if entry is None:
        return None

    pr_url = entry["pr_url"]
    branch = entry["branch"]
    full_repo = entry["full_repo"]
    pr_number = entry["pr_number"]
    project_name = entry["project_name"]

    status, run_id = check_ci_status(branch, full_repo)

    if status == "success":
        remove(instance_dir, pr_url)
        print(f"[ci_queue] CI passed for {pr_url}", file=sys.stderr)
        return f"CI passed: {pr_url}"

    if status == "none":
        remove(instance_dir, pr_url)
        print(f"[ci_queue] No CI runs for {pr_url} — removed", file=sys.stderr)
        return f"No CI: {pr_url}"

    if status == "failure":
        remove(instance_dir, pr_url)
        # Inject a /ci_check mission so the agent can attempt a fix
        _inject_ci_fix_mission(instance_dir, pr_url, project_name)
        print(f"[ci_queue] CI failed for {pr_url} — queued /ci_check mission", file=sys.stderr)
        return f"CI failed: {pr_url} → /ci_check mission queued"

    # status == "pending" — leave in queue
    print(f"[ci_queue] CI still running for {pr_url}", file=sys.stderr)
    return f"CI pending: {pr_url}"


def _inject_ci_fix_mission(instance_dir: str, pr_url: str, project_name: str):
    """Insert a /ci_check mission into the pending queue."""
    from app.utils import insert_pending_mission

    missions_path = Path(instance_dir) / "missions.md"
    entry = f"- [project:{project_name}] /ci_check {pr_url}"
    insert_pending_mission(missions_path, entry, urgent=True)


# ---------------------------------------------------------------------------
# CLI entry point — python3 -m app.ci_queue_runner <url> --project-path <path>
# ---------------------------------------------------------------------------

def main(argv=None):
    """CLI entry point for ci_queue_runner.

    Runs the blocking CI check-and-fix workflow for a single PR.
    This is invoked when a /ci_check mission is picked up by the agent loop.
    """
    import argparse
    import os

    parser = argparse.ArgumentParser(
        description="Check CI status for a PR and attempt fixes if failing."
    )
    parser.add_argument("url", help="PR URL (e.g. https://github.com/owner/repo/pull/42)")
    parser.add_argument("--project-path", required=True, help="Path to the project")
    args = parser.parse_args(argv)

    pr_url = args.url
    project_path = args.project_path

    # Parse PR URL
    from app.github_url_parser import parse_pr_url
    parsed = parse_pr_url(pr_url)
    if not parsed:
        print(json.dumps({"error": f"Invalid PR URL: {pr_url}"}))
        return 1

    full_repo = parsed["full_repo"]
    pr_number = parsed["pr_number"]

    # Fetch PR metadata
    try:
        raw = run_gh(
            "pr", "view", pr_number,
            "--repo", full_repo,
            "--json", "title,headRefName,baseRefName,url,body",
        )
        pr_data = json.loads(raw)
    except Exception as e:
        print(json.dumps({"error": f"Failed to fetch PR: {e}"}))
        return 1

    branch = pr_data.get("headRefName", "")
    base = pr_data.get("baseRefName", "main")

    context = {
        "title": pr_data.get("title", ""),
        "url": pr_data.get("url", pr_url),
        "body": pr_data.get("body", ""),
    }

    # Resolve skill dir for CI fix prompt
    skill_dir = Path(__file__).resolve().parent.parent / "skills" / "core" / "rebase"

    # Reuse the existing CI check-and-fix logic from rebase_pr
    from app.rebase_pr import _run_ci_check_and_fix

    actions_log = []

    def notify_fn(msg):
        print(f"[ci_check] {msg}", file=sys.stderr)

    ci_result = _run_ci_check_and_fix(
        branch=branch,
        base=base,
        full_repo=full_repo,
        pr_number=pr_number,
        project_path=project_path,
        context=context,
        actions_log=actions_log,
        notify_fn=notify_fn,
        skill_dir=skill_dir,
    )

    # Output JSON result for mission_runner
    result = {
        "result": ci_result or "No CI issues found.",
        "actions": actions_log,
        "pr_url": pr_url,
    }
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    sys.exit(main() or 0)
