from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from src import slack_bot


class SlackBotTests(unittest.TestCase):
    def setUp(self) -> None:
        slack_bot._PROCESSED_EVENTS.clear()
        slack_bot._PROCESSED_EVENTS_ORDER.clear()

    def test_split_message_preserves_content(self) -> None:
        message = "\n".join(f"line {index}" for index in range(20))
        chunks = slack_bot._split_message(message, 30)
        self.assertGreater(len(chunks), 1)
        self.assertEqual("\n".join(chunks), message)

    def test_mark_event_processed_deduplicates(self) -> None:
        self.assertTrue(slack_bot._mark_event_processed("event:abc"))
        self.assertFalse(slack_bot._mark_event_processed("event:abc"))

    def test_process_mention_event_uses_thread_root_file(self) -> None:
        sent_messages: list[str] = []
        event = {
            "type": "app_mention",
            "channel": "C123",
            "user": "U123",
            "ts": "1700000001.000200",
            "thread_ts": "1700000000.000100",
            "text": "<@U_BOT> analyze this",
        }
        root_message = {
            "ts": "1700000000.000100",
            "user": "U123",
            "files": [
                {
                    "id": "F123",
                    "name": "error.log",
                    "url_private_download": "https://files.slack.test/error.log",
                    "mimetype": "text/plain",
                }
            ],
        }
        current_message = {
            "ts": "1700000001.000200",
            "user": "U123",
            "text": "<@U_BOT> analyze this",
        }

        def fake_fetch_message(channel: str, ts: str) -> dict[str, object] | None:
            if channel != "C123":
                return None
            if ts == "1700000001.000200":
                return current_message
            if ts == "1700000000.000100":
                return root_message
            return None

        with (
            patch.object(slack_bot, "_fetch_conversation_message", side_effect=fake_fetch_message),
            patch.object(slack_bot, "_fetch_recent_conversation_messages", return_value=[]),
            patch.object(slack_bot, "_download_slack_file", return_value=b"NullReferenceException\nat Example"),
            patch.object(
                slack_bot,
                "_run_analysis_for_text",
                return_value="[로그 분석 결과]\n- 10-20줄 | NullReferenceException | 3회",
            ),
            patch.object(slack_bot, "_post_slack_message", side_effect=lambda _c, _t, text: sent_messages.append(text)),
            patch.object(slack_bot, "_log"),
            patch.dict(os.environ, {"SLACK_ALLOWED_CHANNELS": ""}, clear=False),
        ):
            slack_bot._process_mention_event(event)

        self.assertEqual(len(sent_messages), 2)
        self.assertIn("error.log", sent_messages[0])
        self.assertIn("NullReferenceException", sent_messages[1])

    def test_process_mention_event_without_file_posts_help(self) -> None:
        sent_messages: list[str] = []
        event = {
            "type": "app_mention",
            "channel": "C123",
            "user": "U123",
            "ts": "1700000001.000200",
            "text": "<@U_BOT> analyze this",
        }

        with (
            patch.object(slack_bot, "_fetch_conversation_message", return_value={"ts": event["ts"], "text": event["text"]}),
            patch.object(slack_bot, "_fetch_recent_conversation_messages", return_value=[]),
            patch.object(slack_bot, "_post_slack_message", side_effect=lambda _c, _t, text: sent_messages.append(text)),
            patch.object(slack_bot, "_log"),
            patch.dict(os.environ, {"SLACK_ALLOWED_CHANNELS": ""}, clear=False),
        ):
            slack_bot._process_mention_event(event)

        self.assertEqual(len(sent_messages), 1)
        self.assertIn(".log", sent_messages[0])

    def test_process_mention_event_uses_event_file_without_history_scope(self) -> None:
        sent_messages: list[str] = []
        event = {
            "type": "app_mention",
            "channel": "C123",
            "channel_type": "channel",
            "user": "U123",
            "ts": "1700000001.000200",
            "text": "<@U_BOT> analyze this",
            "files": [
                {
                    "id": "F123",
                    "name": "error.log",
                    "url_private_download": "https://files.slack.test/error.log",
                    "mimetype": "text/plain",
                }
            ],
        }

        with (
            patch.object(
                slack_bot,
                "_fetch_conversation_message",
                side_effect=slack_bot.SlackApiError("conversations.history", "missing_scope"),
            ),
            patch.object(
                slack_bot,
                "_fetch_recent_conversation_messages",
                side_effect=slack_bot.SlackApiError("conversations.history", "missing_scope"),
            ),
            patch.object(slack_bot, "_download_slack_file", return_value=b"NullReferenceException\nat Example"),
            patch.object(
                slack_bot,
                "_run_analysis_for_text",
                return_value="[로그 분석 결과]\n- 10-20줄 | NullReferenceException | 3회",
            ),
            patch.object(slack_bot, "_post_slack_message", side_effect=lambda _c, _t, text: sent_messages.append(text)),
            patch.object(slack_bot, "_log"),
            patch.dict(os.environ, {"SLACK_ALLOWED_CHANNELS": ""}, clear=False),
        ):
            slack_bot._process_mention_event(event)

        self.assertEqual(len(sent_messages), 2)
        self.assertIn("error.log", sent_messages[0])
        self.assertIn("NullReferenceException", sent_messages[1])

    def test_process_mention_event_missing_scope_posts_guidance(self) -> None:
        sent_messages: list[str] = []
        event = {
            "type": "app_mention",
            "channel": "C123",
            "channel_type": "channel",
            "user": "U123",
            "ts": "1700000001.000200",
            "text": "<@U_BOT> analyze this",
        }

        with (
            patch.object(
                slack_bot,
                "_fetch_conversation_message",
                side_effect=slack_bot.SlackApiError("conversations.history", "missing_scope"),
            ),
            patch.object(
                slack_bot,
                "_fetch_recent_conversation_messages",
                side_effect=slack_bot.SlackApiError("conversations.history", "missing_scope"),
            ),
            patch.object(slack_bot, "_post_slack_message", side_effect=lambda _c, _t, text: sent_messages.append(text)),
            patch.object(slack_bot, "_log"),
            patch.dict(os.environ, {"SLACK_ALLOWED_CHANNELS": ""}, clear=False),
        ):
            slack_bot._process_mention_event(event)

        self.assertEqual(len(sent_messages), 1)
        self.assertIn("channels:history", sent_messages[0])
        self.assertIn("Reinstall to Workspace", sent_messages[0])


if __name__ == "__main__":
    unittest.main()
