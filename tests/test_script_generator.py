"""Tests for script_generator: templates, suppression, cleaning, truncation."""

import pytest

from script_generator import (
    WebhookEvent,
    clean_text,
    generate_script,
    is_suppressed,
    truncate_words,
)


# --- Template selection ---


class TestTemplateCompleted:
    def test_completed_with_agent(self):
        event = WebhookEvent(kind="task.completed", agent="eng1", detail="auth refactor")
        assert generate_script(event) == "Completed: auth refactor"

    def test_completed_without_agent(self):
        event = WebhookEvent(kind="task.completed", detail="auth refactor")
        assert generate_script(event) == "Completed: auth refactor"

    def test_agent_not_in_text(self):
        event = WebhookEvent(kind="task.completed", agent="eng1", detail="auth refactor")
        assert "eng1" not in generate_script(event)


class TestTemplateFailed:
    def test_failed_with_agent(self):
        event = WebhookEvent(kind="build.failed", agent="eng1", detail="tests broken")
        assert generate_script(event) == "Heads up, failure: tests broken"

    def test_failed_without_agent(self):
        event = WebhookEvent(kind="build.failed", detail="tests broken")
        assert generate_script(event) == "Heads up, failure: tests broken"


class TestTemplateStuck:
    def test_stuck_with_agent(self):
        event = WebhookEvent(kind="agent.stuck", agent="eng2", detail="waiting on API")
        assert generate_script(event) == "Something is stuck. waiting on API"

    def test_stuck_without_agent(self):
        event = WebhookEvent(kind="agent.stuck", detail="waiting on API")
        assert generate_script(event) == "Something is stuck. waiting on API"


class TestTemplateStarted:
    def test_started_with_agent(self):
        event = WebhookEvent(kind="agent.started", agent="eng1")
        assert generate_script(event) == "Work started"

    def test_started_without_agent(self):
        event = WebhookEvent(kind="agent.started")
        assert generate_script(event) == "Work started"


class TestTemplateStopped:
    def test_stopped_with_agent(self):
        event = WebhookEvent(kind="agent.stopped", agent="eng1")
        assert generate_script(event) == "Work stopped"

    def test_stopped_without_agent(self):
        event = WebhookEvent(kind="agent.stopped")
        assert generate_script(event) == "Work stopped"


class TestTemplateDefault:
    def test_default_with_agent(self):
        event = WebhookEvent(kind="custom.event", agent="pm", detail="standup in 5")
        assert generate_script(event) == "standup in 5"

    def test_default_without_agent(self):
        event = WebhookEvent(kind="custom.event", detail="standup in 5")
        assert generate_script(event) == "standup in 5"

    def test_empty_kind_treated_as_default(self):
        event = WebhookEvent(kind="", agent="eng1", detail="hello world")
        assert generate_script(event) == "hello world"

    def test_empty_kind_no_agent_no_detail_returns_none(self):
        event = WebhookEvent(kind="", detail="")
        assert generate_script(event) is None


# --- Suppression ---


class TestSuppression:
    def test_idle_suppressed(self):
        event = WebhookEvent(kind="agent.idle", agent="eng1", detail="nothing happening")
        result = generate_script(event, suppress_kinds=["*.idle"])
        assert result is None

    def test_message_suppressed(self):
        event = WebhookEvent(kind="agent.message", agent="eng1", detail="chat msg")
        result = generate_script(event, suppress_kinds=["*.message"])
        assert result is None

    def test_non_matching_not_suppressed(self):
        event = WebhookEvent(kind="agent.completed", agent="eng1", detail="done")
        result = generate_script(event, suppress_kinds=["*.idle"])
        assert result is not None

    def test_exact_match_suppression(self):
        event = WebhookEvent(kind="idle", detail="x")
        # "*.idle" should NOT match bare "idle" (fnmatch requires the dot)
        result = generate_script(event, suppress_kinds=["*.idle"])
        assert result is not None

    def test_multiple_patterns(self):
        event = WebhookEvent(kind="system.idle", detail="x")
        result = generate_script(event, suppress_kinds=["*.message", "*.idle"])
        assert result is None


class TestIsSuppressed:
    def test_glob_star_dot_idle(self):
        assert is_suppressed("agent.idle", ["*.idle"]) is True

    def test_glob_no_match(self):
        assert is_suppressed("agent.completed", ["*.idle"]) is False

    def test_bare_kind_no_dot(self):
        # "idle" does not match "*.idle" because * doesn't match empty before dot
        assert is_suppressed("idle", ["*.idle"]) is False

    def test_empty_patterns(self):
        assert is_suppressed("anything", []) is False


# --- clean_text ---


class TestCleanText:
    def test_strips_code_fences(self):
        text = "before ```python\nprint('hello')\n``` after"
        assert clean_text(text) == "before after"

    def test_strips_inline_code(self):
        assert clean_text("run `npm install` now") == "run now"

    def test_strips_urls(self):
        text = "see https://github.com/org/repo/pull/123 for details"
        assert clean_text(text) == "see for details"

    def test_truncates_long_commit_hash(self):
        text = "commit abc123def456789012345678901234567890"
        result = clean_text(text)
        assert "abc123de" in result
        # The full 40-char hash should not survive
        assert "abc123def456789012345678901234567890" not in result

    def test_preserves_short_hex(self):
        # 8 chars or fewer should stay untouched
        text = "version a1b2c3d4 released"
        assert clean_text(text) == "version a1b2c3d4 released"

    def test_strips_markdown_bold(self):
        assert clean_text("this is **important**") == "this is important"

    def test_strips_markdown_italic(self):
        assert clean_text("this is *italic*") == "this is italic"

    def test_strips_heading_markers(self):
        assert clean_text("## Overview\nSome text") == "Overview Some text"

    def test_strips_markdown_links(self):
        assert clean_text("[click here](http://example.com)") == "click here"

    def test_collapses_whitespace(self):
        assert clean_text("too   many    spaces") == "too many spaces"

    def test_empty_string(self):
        assert clean_text("") == ""

    def test_none_like_empty(self):
        # Not None but empty
        assert clean_text("") == ""

    def test_mixed_markdown(self):
        text = "## Title\n**Bold** and `code` with https://url.com"
        result = clean_text(text)
        assert result == "Title Bold and with"


# --- truncate_words ---


class TestTruncateWords:
    def test_under_limit(self):
        assert truncate_words("one two three", 5) == "one two three"

    def test_at_limit(self):
        assert truncate_words("one two three", 3) == "one two three"

    def test_over_limit(self):
        assert truncate_words("one two three four five", 3) == "one two three"

    def test_single_word(self):
        assert truncate_words("hello", 1) == "hello"

    def test_empty_string(self):
        assert truncate_words("", 10) == ""

    def test_does_not_cut_mid_word(self):
        result = truncate_words("implementation details are complex", 2)
        assert result == "implementation details"
        # Should not contain partial word
        assert "are" not in result


# --- Integration: generate_script with cleaning ---


class TestGenerateScriptIntegration:
    def test_markdown_stripped_in_completed(self):
        event = WebhookEvent(
            kind="task.completed",
            agent="eng1",
            detail="Fixed **auth** bug in `login.py`",
        )
        result = generate_script(event)
        assert result == "Completed: Fixed auth bug in"

    def test_url_stripped_in_detail(self):
        event = WebhookEvent(
            kind="build.failed",
            detail="See https://ci.example.com/build/123 for logs",
        )
        result = generate_script(event)
        assert result == "Heads up, failure: See for logs"

    def test_long_hash_truncated(self):
        event = WebhookEvent(
            kind="task.completed",
            agent="eng2",
            detail="merged abc123def456789012345678901234567890",
        )
        result = generate_script(event)
        assert "abc123de" in result
        assert "abc123def456789012345678901234567890" not in result

    def test_truncation_applied(self):
        long_detail = " ".join(f"word{i}" for i in range(50))
        event = WebhookEvent(kind="custom.event", detail=long_detail)
        result = generate_script(event, max_words=10)
        assert len(result.split()) == 10

    def test_full_pipeline_suppress_and_clean(self):
        event = WebhookEvent(kind="agent.idle", detail="**nothing** happening")
        result = generate_script(event, suppress_kinds=["*.idle"])
        assert result is None
