"""HTML comment markers for stateful PR interactions.

Defines named string constants for each marker type and string manipulation
helpers for reading and writing tagged sections in GitHub comment bodies.

All consumers (review_runner.py, future incremental review, progress
indicators) import the same constants — no drift between writer and reader.

Tag format: ``<!-- koan-{name} -->``
GitHub's 65536-char comment limit constrains total comment size; keep
marker strings short.  No ``--`` inside comment bodies (invalid HTML).
"""

import re
from typing import List, Optional


# ---------------------------------------------------------------------------
# Marker constants
# ---------------------------------------------------------------------------

SUMMARY_TAG = "<!-- koan-summary -->"
"""Top-level marker that identifies the bot's summary comment on a PR."""

COMMIT_IDS_START = "<!-- koan-commits-start -->"
COMMIT_IDS_END = "<!-- koan-commits-end -->"
"""Hidden block storing reviewed commit SHAs (one per line)."""

RAW_SUMMARY_START = "<!-- koan-raw-summary-start -->"
RAW_SUMMARY_END = "<!-- koan-raw-summary-end -->"
"""Hidden block caching the raw changeset summary for prompt injection."""

SHORT_SUMMARY_START = "<!-- koan-short-summary-start -->"
SHORT_SUMMARY_END = "<!-- koan-short-summary-end -->"
"""Short summary block injected into future prompts."""

RELEASE_NOTES_START = "<!-- koan-release-notes-start -->"
RELEASE_NOTES_END = "<!-- koan-release-notes-end -->"
"""Auto-generated release notes block in the PR description."""



# ---------------------------------------------------------------------------
# String manipulation helpers
# ---------------------------------------------------------------------------

def extract_between_markers(body: str, start: str, end: str) -> Optional[str]:
    """Return the text between ``start`` and ``end`` markers.

    Args:
        body: The full comment body to search.
        start: Opening marker string (e.g. ``COMMIT_IDS_START``).
        end: Closing marker string (e.g. ``COMMIT_IDS_END``).

    Returns:
        The text between the markers (stripped), or ``None`` if either
        marker is absent or the start marker appears after the end marker.
    """
    start_idx = body.find(start)
    if start_idx == -1:
        return None
    end_idx = body.find(end, start_idx + len(start))
    if end_idx == -1:
        return None
    return body[start_idx + len(start):end_idx]


def remove_section(body: str, start: str, end: str) -> str:
    """Strip a tagged block (including the markers) from ``body``.

    If the markers are absent the original body is returned unchanged.
    Leading/trailing whitespace around the removed block is preserved to
    avoid creating double blank lines.

    Args:
        body: The full comment body.
        start: Opening marker string.
        end: Closing marker string.

    Returns:
        Body with the tagged section removed.
    """
    start_idx = body.find(start)
    if start_idx == -1:
        return body
    end_idx = body.find(end, start_idx + len(start))
    if end_idx == -1:
        return body
    return body[:start_idx] + body[end_idx + len(end):]


def wrap_section(content: str, start: str, end: str) -> str:
    """Wrap ``content`` between ``start`` and ``end`` marker tags.

    Args:
        content: The text to wrap.
        start: Opening marker string.
        end: Closing marker string.

    Returns:
        ``"{start}{content}{end}"``
    """
    return f"{start}{content}{end}"


def replace_section(body: str, start: str, end: str, new_content: str) -> str:
    """Idempotent upsert of a tagged block in ``body``.

    If the block already exists it is replaced; otherwise the block is
    appended to the end of ``body``.

    Args:
        body: The full comment body.
        start: Opening marker string.
        end: Closing marker string.
        new_content: New content to place between the markers.

    Returns:
        Updated body with the tagged section containing ``new_content``.
    """
    new_block = wrap_section(new_content, start, end)
    start_idx = body.find(start)
    if start_idx == -1:
        # Block absent — append
        return body + new_block
    end_idx = body.find(end, start_idx + len(start))
    if end_idx == -1:
        # Malformed (start present, end absent) — append block, leave orphan
        return body + new_block
    return body[:start_idx] + new_block + body[end_idx + len(end):]


# ---------------------------------------------------------------------------
# Hidden commit block (single HTML comment — fully invisible on GitHub)
# ---------------------------------------------------------------------------

_HIDDEN_COMMITS_TAG = "koan-commits"
_HIDDEN_COMMITS_RE = re.compile(
    r"<!-- " + _HIDDEN_COMMITS_TAG + r"\n(.*?)\n-->",
    re.DOTALL,
)


def build_hidden_commit_block(shas: List[str]) -> str:
    """Build a single HTML comment containing all commit SHAs.

    Unlike the legacy two-marker format (where SHAs between separate
    ``<!-- start -->`` and ``<!-- end -->`` comments render visibly),
    this embeds everything inside one comment — fully hidden on GitHub.
    """
    return f"<!-- {_HIDDEN_COMMITS_TAG}\n" + "\n".join(shas) + "\n-->"


def extract_commit_shas(body: str) -> List[str]:
    """Extract reviewed commit SHAs from a comment body.

    Tries the new single-comment format first, then falls back to the
    legacy two-marker format for backward compatibility with older reviews.
    """
    m = _HIDDEN_COMMITS_RE.search(body)
    if m:
        return [s.strip() for s in m.group(1).splitlines() if s.strip()]

    raw = extract_between_markers(body, COMMIT_IDS_START, COMMIT_IDS_END)
    if raw:
        return [s.strip() for s in raw.splitlines() if s.strip()]

    return []


def replace_commit_block(body: str, shas: List[str]) -> str:
    """Replace or append the hidden commit block in ``body``.

    Removes any legacy two-marker block first, then upserts the new
    single-comment format.
    """
    body = remove_section(body, COMMIT_IDS_START, COMMIT_IDS_END)

    m = _HIDDEN_COMMITS_RE.search(body)
    new_block = build_hidden_commit_block(shas)
    if m:
        return body[:m.start()] + new_block + body[m.end():]
    return body + "\n" + new_block


# ---------------------------------------------------------------------------
# Prior-review extraction (turn a posted koan-summary comment back into the
# human-readable review text, for re-injection as context on re-review)
# ---------------------------------------------------------------------------


def strip_hidden_sections(body: str) -> str:
    """Remove all machine-only marker blocks from a comment body.

    Strips the hidden commit block (both the single-comment and legacy
    two-marker forms) plus the raw/short-summary and release-notes blocks, so
    only human-readable content remains. Markers that are absent are no-ops.
    """
    body = _HIDDEN_COMMITS_RE.sub("", body)
    for start, end in (
        (COMMIT_IDS_START, COMMIT_IDS_END),
        (RAW_SUMMARY_START, RAW_SUMMARY_END),
        (SHORT_SUMMARY_START, SHORT_SUMMARY_END),
        (RELEASE_NOTES_START, RELEASE_NOTES_END),
    ):
        body = remove_section(body, start, end)
    return body


def extract_prior_review_body(body: str) -> str:
    """Recover the readable review text from a posted ``koan-summary`` comment.

    The bot posts its review as ``{SUMMARY_TAG}\\n[## Code Review\\n\\n]<text>``
    followed by a ``\\n---\\n<footer>`` and a hidden commit block. This reverses
    that: strip the hidden blocks, drop a leading ``SUMMARY_TAG``, and drop the
    footer (everything from the LAST ``\\n---`` onward, so a review whose own
    body contains ``---`` rules keeps them). Returns ``""`` when nothing
    readable remains.
    """
    if not body:
        return ""
    text = strip_hidden_sections(body).strip()
    if text.startswith(SUMMARY_TAG):
        text = text[len(SUMMARY_TAG):]
    footer_idx = text.rfind("\n---")
    if footer_idx != -1:
        text = text[:footer_idx]
    return text.strip()
