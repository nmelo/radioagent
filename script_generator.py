"""Event JSON to natural-language announcement text.

Translates webhook events into sentences suitable for TTS. Uses templates
(not an LLM) for sub-millisecond latency. Supports glob-based suppression,
markdown/URL stripping, and word-boundary truncation.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from fnmatch import fnmatch


@dataclass
class WebhookEvent:
    """Inbound event from a webhook POST."""

    detail: str = ""
    kind: str = ""
    agent: str = ""


# Regex patterns for clean_text
_CODE_FENCE_RE = re.compile(r"```[\s\S]*?```")
_INLINE_CODE_RE = re.compile(r"`[^`]+`")
_URL_RE = re.compile(r"https?://\S+")
_LONG_HASH_RE = re.compile(r"\b[0-9a-f]{9,40}\b")
_MARKDOWN_BOLD_RE = re.compile(r"\*\*(.+?)\*\*")
_MARKDOWN_ITALIC_RE = re.compile(r"\*(.+?)\*")
_MARKDOWN_HEADING_RE = re.compile(r"^#{1,6}\s+", re.MULTILINE)
_MARKDOWN_LINK_RE = re.compile(r"\[([^\]]+)\]\([^)]+\)")
_WHITESPACE_RE = re.compile(r"\s+")


def clean_text(text: str) -> str:
    """Strip markdown formatting, URLs, code fences, and long commit hashes.

    Commit hashes longer than 8 hex chars are truncated to 8. The goal is
    text that sounds natural when read aloud by a TTS engine.
    """
    if not text:
        return ""

    result = text

    # Strip code fences (``` blocks) first, they can contain anything
    result = _CODE_FENCE_RE.sub("", result)

    # Strip inline code
    result = _INLINE_CODE_RE.sub("", result)

    # Strip markdown links before bare URLs: [text](url) -> text
    result = _MARKDOWN_LINK_RE.sub(r"\1", result)

    # Strip bare URLs
    result = _URL_RE.sub("", result)

    # Truncate long hex hashes to 8 chars
    result = _LONG_HASH_RE.sub(lambda m: m.group(0)[:8], result)

    # Strip bold/italic markers
    result = _MARKDOWN_BOLD_RE.sub(r"\1", result)
    result = _MARKDOWN_ITALIC_RE.sub(r"\1", result)

    # Strip heading markers
    result = _MARKDOWN_HEADING_RE.sub("", result)

    # Collapse whitespace
    result = _WHITESPACE_RE.sub(" ", result).strip()

    return result


def truncate_words(text: str, max_words: int) -> str:
    """Truncate text to max_words at a word boundary."""
    if not text:
        return ""
    words = text.split()
    if len(words) <= max_words:
        return text
    return " ".join(words[:max_words])


def is_suppressed(kind: str, suppress_kinds: list[str]) -> bool:
    """Check if an event kind matches any suppression glob pattern."""
    for pattern in suppress_kinds:
        if fnmatch(kind, pattern):
            return True
    return False


def generate_script(
    event: WebhookEvent,
    suppress_kinds: list[str] | None = None,
    max_words: int = 40,
) -> str | None:
    """Convert a webhook event to announcement text, or None if suppressed.

    Args:
        event: The inbound webhook event.
        suppress_kinds: Glob patterns for kinds to silently ignore.
        max_words: Maximum word count for the announcement.

    Returns:
        Announcement string, or None if the event should be suppressed.
    """
    if suppress_kinds is None:
        suppress_kinds = []

    kind = event.kind or ""

    # Check suppression
    if kind and is_suppressed(kind, suppress_kinds):
        return None

    # Clean and truncate the detail text
    detail = truncate_words(clean_text(event.detail), max_words)
    agent = event.agent.strip() if event.agent else ""

    # Empty kind treated as default
    if not kind:
        kind = "custom"

    # Template selection by kind suffix
    if kind.endswith(".completed"):
        if agent:
            return f"{agent} finished: {detail}"
        return f"Completed: {detail}"

    if kind.endswith(".failed"):
        if agent:
            return f"Heads up. {agent} hit a failure: {detail}"
        return f"Failure: {detail}"

    if kind.endswith(".stuck"):
        if agent:
            return f"{agent} appears stuck. {detail}"
        return f"Something is stuck. {detail}"

    if kind.endswith(".started"):
        if agent:
            return f"{agent} started working"
        return "Work started"

    if kind.endswith(".stopped"):
        if agent:
            return f"{agent} stopped"
        return "Work stopped"

    # Default: detail verbatim (cleaned and truncated)
    if agent:
        return f"{agent}: {detail}"
    return detail if detail else None
