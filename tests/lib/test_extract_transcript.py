#!/usr/bin/env python3
"""Tests for extract_transcript.py"""
import unittest
import sys
import os

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.abspath(os.path.join(SCRIPT_DIR, "..", ".."))
sys.path.insert(0, os.path.join(REPO_ROOT, "hooks", "lib"))
from extract_transcript import (
    is_visible, is_continuation, filter_visible, detect_continuations, get_content,
)

# --- Test helpers ---

def user_text(text):
    return {"role": "user", "content": text}

def user_tool_results():
    return {"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": "x", "content": "ok"}
    ]}

def assistant_text(text):
    return {"role": "assistant", "content": [{"type": "text", "text": text}]}

def assistant_tool_only():
    return {"role": "assistant", "content": [
        {"type": "tool_use", "id": "x", "name": "Read", "input": {"file_path": "/foo"}}
    ]}

def system_msg(text, subtype=""):
    msg = {"type": "system", "content": text}
    if subtype:
        msg["subtype"] = subtype
    return msg

def continuation_msg():
    return {"role": "user", "content":
        "This session is being continued from a previous conversation "
        "that ran out of context. The summary below covers the earlier portion."}


class TestIsVisible(unittest.TestCase):

    def test_user_text_visible(self):
        self.assertTrue(is_visible(user_text("fix the bug")))

    def test_user_empty_not_visible(self):
        self.assertFalse(is_visible(user_text("")))
        self.assertFalse(is_visible(user_text("   ")))

    def test_user_tool_result_not_visible(self):
        self.assertFalse(is_visible(user_tool_results()))

    def test_user_system_xml_not_visible(self):
        self.assertFalse(is_visible(user_text("<system-reminder>injected</system-reminder>")))
        self.assertFalse(is_visible(user_text("<local-command-caveat>noise</local-command-caveat>")))
        self.assertFalse(is_visible(user_text("<command-name>/clear</command-name>")))

    def test_assistant_with_text_visible(self):
        self.assertTrue(is_visible(assistant_text("Here is the fix.")))

    def test_assistant_tool_only_not_visible(self):
        self.assertFalse(is_visible(assistant_tool_only()))

    def test_assistant_empty_text_not_visible(self):
        self.assertFalse(is_visible({"role": "assistant", "content": [
            {"type": "text", "text": "   "}
        ]}))

    def test_system_not_visible(self):
        self.assertFalse(is_visible(system_msg("Conversation compacted", "compact_boundary")))
        self.assertFalse(is_visible(system_msg("summary", "away_summary")))

    def test_attachment_not_visible(self):
        self.assertFalse(is_visible({"type": "attachment", "content": ""}))

    def test_continuation_is_visible(self):
        """Continuation messages are user messages — they pass visibility."""
        self.assertTrue(is_visible(continuation_msg()))


class TestIsContinuation(unittest.TestCase):

    def test_detects_continuation(self):
        self.assertTrue(is_continuation(continuation_msg()))

    def test_rejects_normal_user(self):
        self.assertFalse(is_continuation(user_text("fix the bug")))

    def test_rejects_assistant(self):
        self.assertFalse(is_continuation(assistant_text("This session is being continued")))

    def test_rejects_system(self):
        self.assertFalse(is_continuation(system_msg("This session is being continued")))


class TestFilterVisible(unittest.TestCase):

    def test_filters_mixed_messages(self):
        msgs = [
            system_msg("init"),           # 0: filtered
            user_text("hello"),           # 1: visible
            assistant_tool_only(),        # 2: filtered
            assistant_text("hi"),         # 3: visible
            user_tool_results(),          # 4: filtered
            user_text("bye"),             # 5: visible
        ]
        result = filter_visible(msgs)
        self.assertEqual(len(result), 3)
        self.assertEqual(result[0], (1, msgs[1]))
        self.assertEqual(result[1], (3, msgs[3]))
        self.assertEqual(result[2], (5, msgs[5]))


class TestDetectContinuations(unittest.TestCase):

    def test_finds_continuation_indices(self):
        visible = [
            (0, user_text("hello")),
            (5, continuation_msg()),
            (10, assistant_text("hi")),
        ]
        result = detect_continuations(visible)
        self.assertEqual(result, {1})  # index 1 in visible list

    def test_no_continuations(self):
        visible = [
            (0, user_text("hello")),
            (3, assistant_text("hi")),
        ]
        result = detect_continuations(visible)
        self.assertEqual(result, set())


from extract_transcript import sample_messages


class TestSampleMessages(unittest.TestCase):

    def _make_visible(self, n, continuation_at=None):
        """Create n visible entries. Optionally place a continuation at given index."""
        visible = []
        for i in range(n):
            if continuation_at is not None and i == continuation_at:
                visible.append((i * 3, continuation_msg()))
            elif i % 2 == 0:
                visible.append((i * 3, user_text(f"msg {i}")))
            else:
                visible.append((i * 3, assistant_text(f"reply {i}")))
        return visible

    def test_take_all_under_base(self):
        """Sessions with ≤ head+tail visible messages: take all."""
        visible = self._make_visible(25)
        selected, gaps = sample_messages(visible, set(), 10, 20, 30, 100)
        self.assertEqual(len(selected), 25)
        self.assertEqual(gaps, [])

    def test_head_tail_no_middle(self):
        """Sessions with head+tail < visible ≤ middleScaleStart: head + tail only."""
        visible = self._make_visible(80)
        selected, gaps = sample_messages(visible, set(), 10, 20, 30, 100)
        self.assertEqual(len(selected), 30)
        # First 10 are head
        self.assertEqual([s[0] for s in selected[:10]], list(range(10)))
        # Last 20 are tail
        self.assertEqual([s[0] for s in selected[10:]], list(range(60, 80)))
        # One gap
        self.assertEqual(len(gaps), 1)
        self.assertEqual(gaps[0][2], 50)  # 50 messages omitted

    def test_middle_scales_with_size(self):
        """Sessions > middleScaleStart get a contiguous middle block."""
        visible = self._make_visible(200)
        selected, gaps = sample_messages(visible, set(), 10, 20, 30, 100)
        # M = min(30, round((200-100)*30/150)) = min(30, 20) = 20
        self.assertEqual(len(selected), 50)  # 10 + 20 + 20
        # Verify middle is centered: center=100, mid_start=90, mid_end=110
        middle_indices = [s[0] for s in selected[10:30]]
        self.assertEqual(middle_indices[0], 90)
        self.assertEqual(middle_indices[-1], 109)
        # Two gaps: head-to-middle, middle-to-tail
        self.assertEqual(len(gaps), 2)

    def test_middle_caps_at_max(self):
        """Middle size caps at middleMaxSize."""
        visible = self._make_visible(300)
        selected, gaps = sample_messages(visible, set(), 10, 20, 30, 100)
        # M = min(30, round((300-100)*30/150)) = min(30, 40) = 30
        self.assertEqual(len(selected), 60)  # 10 + 30 + 20

    def test_path_a_gap_continuation(self):
        """Continuation in the gap eats from tail budget."""
        visible = self._make_visible(80, continuation_at=40)
        cont_indices = {40}
        selected, gaps = sample_messages(visible, cont_indices, 10, 20, 30, 100)
        # 10 head + 1 gap continuation + 19 tail = 30
        self.assertEqual(len(selected), 30)
        # Continuation message is in the selection
        cont_msgs = [s for s in selected if is_continuation(s[2])]
        self.assertEqual(len(cont_msgs), 1)

    def test_path_a_continuation_in_head(self):
        """Continuation inside head window — no tail adjustment."""
        visible = self._make_visible(80, continuation_at=5)
        cont_indices = {5}
        selected, gaps = sample_messages(visible, cont_indices, 10, 20, 30, 100)
        # Continuation is in head, 0 gap continuations, tail stays 20
        self.assertEqual(len(selected), 30)

    def test_path_a_continuation_in_tail(self):
        """Continuation inside tail window — no tail adjustment."""
        visible = self._make_visible(80, continuation_at=75)
        cont_indices = {75}
        selected, gaps = sample_messages(visible, cont_indices, 10, 20, 30, 100)
        # Continuation is in tail, 0 gap continuations, tail stays 20
        self.assertEqual(len(selected), 30)

    def test_tail_floor(self):
        """Many gap continuations don't shrink tail below floor of 10."""
        visible = self._make_visible(80)
        # Fake 15 gap continuations scattered in the gap (indices 20-59)
        cont_indices = set(range(20, 35))
        selected, gaps = sample_messages(visible, cont_indices, 10, 20, 30, 100)
        # Max gap conts = tailSize - tailFloor = 20 - 10 = 10
        # So only 10 gap conts kept, tail shrinks to 10
        # Total = 10 head + 10 gap conts + 10 tail = 30
        tail_start = 80 - 10  # tail of 10
        tail_indices = [s[0] for s in selected if s[0] >= tail_start]
        self.assertGreaterEqual(len(tail_indices), 10)


from extract_transcript import extract_text, format_markdown, count_session_words


class TestExtractText(unittest.TestCase):

    def test_string_content(self):
        msg = user_text("hello world")
        self.assertEqual(extract_text(msg, 2000), "hello world")

    def test_list_content(self):
        msg = assistant_text("hello world")
        self.assertEqual(extract_text(msg, 2000), "hello world")

    def test_truncation(self):
        msg = user_text("a" * 100)
        result = extract_text(msg, 50)
        self.assertTrue(result.startswith("a" * 50))
        self.assertIn("[truncated]", result)

    def test_nested_message_format(self):
        msg = {"type": "assistant", "message": {"role": "assistant", "content": "nested"}}
        self.assertEqual(extract_text(msg, 2000), "nested")


class TestFormatMarkdown(unittest.TestCase):

    def test_basic_format(self):
        selected = [
            (0, 0, user_text("hello")),
            (1, 3, assistant_text("hi")),
        ]
        md, turns = format_markdown(selected, [], "test-session", 2000)
        self.assertIn("# Session Transcript: test-session", md)
        self.assertIn("## User\n\nhello", md)
        self.assertIn("## Assistant\n\nhi", md)
        self.assertEqual(turns, 2)

    def test_gap_markers(self):
        selected = [
            (0, 0, user_text("start")),
            (50, 150, user_text("end")),
        ]
        gaps = [(0, 50, 49)]
        md, turns = format_markdown(selected, gaps, "test-session", 2000)
        self.assertIn("[... 49 messages omitted ...]", md)

    def test_multiple_gaps(self):
        selected = [
            (0, 0, user_text("head")),
            (50, 150, user_text("middle")),
            (90, 270, user_text("tail")),
        ]
        gaps = [(0, 50, 49), (50, 90, 39)]
        md, _ = format_markdown(selected, gaps, "s", 2000)
        self.assertIn("[... 49 messages omitted ...]", md)
        self.assertIn("[... 39 messages omitted ...]", md)


class TestCountSessionWords(unittest.TestCase):

    def test_counts_user_and_assistant(self):
        msgs = [
            user_text("one two three"),
            assistant_text("four five"),
            user_tool_results(),  # not counted
            assistant_tool_only(),  # not counted
        ]
        self.assertEqual(count_session_words(msgs), 5)

    def test_strips_system_xml(self):
        msgs = [
            user_text("<system-reminder>ignored</system-reminder> real words here"),
        ]
        self.assertEqual(count_session_words(msgs), 3)


if __name__ == '__main__':
    unittest.main()
