"""Persistent CI check queue.

Decouples CI monitoring from the rebase workflow — after pushing a rebased
branch, Kōan enqueues a CI check entry instead of blocking for 10-30 minutes.
The main loop drains one entry per iteration via ``ci_queue_runner.drain_one()``.

File location: ``instance/.ci-queue.json``
Lock file:     ``instance/.ci-queue.lock``

Follows the same fcntl locking + atomic_write pattern as ``check_tracker.py``.
"""

import fcntl
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional


def _queue_path(instance_dir) -> Path:
    """Return path to the CI queue file."""
    return Path(instance_dir) / ".ci-queue.json"


def _lock_path(instance_dir) -> Path:
    """Return path to the CI queue lock file."""
    return Path(instance_dir) / ".ci-queue.lock"


def _load(instance_dir) -> List[dict]:
    """Load the queue from disk. Returns a list of queue entries."""
    path = _queue_path(instance_dir)
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text())
        if isinstance(data, list):
            return data
        return []
    except (json.JSONDecodeError, OSError):
        return []


def _save(instance_dir, entries: List[dict]):
    """Persist queue to disk (atomic write)."""
    from app.utils import atomic_write

    path = _queue_path(instance_dir)
    atomic_write(path, json.dumps(entries, indent=2) + "\n")


def _with_lock(instance_dir, fn):
    """Execute fn under an exclusive file lock and return its result."""
    lock = _lock_path(instance_dir)
    with open(lock, "a") as lf:
        fcntl.flock(lf, fcntl.LOCK_EX)
        try:
            return fn()
        finally:
            fcntl.flock(lf, fcntl.LOCK_UN)


def enqueue(
    instance_dir,
    *,
    pr_url: str,
    branch: str,
    full_repo: str,
    pr_number: str,
    project_name: str,
    project_path: str,
) -> bool:
    """Add a CI check entry to the queue. Returns False if already queued (dedup by pr_url)."""
    def _do():
        entries = _load(instance_dir)
        # Dedup: skip if this PR is already in the queue
        for entry in entries:
            if entry.get("pr_url") == pr_url:
                return False
        entries.append({
            "pr_url": pr_url,
            "branch": branch,
            "full_repo": full_repo,
            "pr_number": pr_number,
            "project_name": project_name,
            "project_path": project_path,
            "enqueued_at": datetime.now(timezone.utc).isoformat(),
            "attempts": 0,
        })
        _save(instance_dir, entries)
        return True

    return _with_lock(instance_dir, _do)


def remove(instance_dir, pr_url: str) -> bool:
    """Remove an entry by pr_url. Returns True if found and removed."""
    def _do():
        entries = _load(instance_dir)
        before = len(entries)
        entries = [e for e in entries if e.get("pr_url") != pr_url]
        if len(entries) < before:
            _save(instance_dir, entries)
            return True
        return False

    return _with_lock(instance_dir, _do)


def peek(instance_dir) -> Optional[dict]:
    """Return the oldest queue entry without removing it, or None."""
    entries = _load(instance_dir)
    return entries[0] if entries else None


def list_entries(instance_dir) -> List[dict]:
    """Return all queue entries."""
    return _load(instance_dir)


def increment_attempts(instance_dir, pr_url: str) -> int:
    """Increment the attempt counter for an entry. Returns new count."""
    def _do():
        entries = _load(instance_dir)
        for entry in entries:
            if entry.get("pr_url") == pr_url:
                entry["attempts"] = entry.get("attempts", 0) + 1
                _save(instance_dir, entries)
                return entry["attempts"]
        return 0

    return _with_lock(instance_dir, _do)


MAX_AGE_HOURS = 24


def remove_stale(instance_dir) -> List[dict]:
    """Remove entries older than MAX_AGE_HOURS. Returns removed entries."""
    def _do():
        entries = _load(instance_dir)
        now = datetime.now(timezone.utc)
        keep = []
        removed = []
        for entry in entries:
            try:
                enqueued = datetime.fromisoformat(entry["enqueued_at"])
                age_hours = (now - enqueued).total_seconds() / 3600
                if age_hours > MAX_AGE_HOURS:
                    removed.append(entry)
                    continue
            except (KeyError, ValueError):
                removed.append(entry)
                continue
            keep.append(entry)
        if removed:
            _save(instance_dir, keep)
        return removed

    return _with_lock(instance_dir, _do)
