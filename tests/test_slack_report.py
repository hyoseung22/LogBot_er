from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from src import slack_bot
from src.log_parser import build_log_snapshot_from_text
from src.slack_report import build_slack_log_report, format_slack_log_report


class SlackReportTests(unittest.TestCase):
    def test_build_report_groups_errors_from_entire_file(self) -> None:
        lines = [f"info line {index}" for index in range(1, 3501)]
        lines.append("NullReferenceException: Object reference not set to an instance of an object")
        lines.append("continuation")
        lines.append("NullReferenceException: Object reference not set to an instance of an object")
        snapshot = build_log_snapshot_from_text(
            path_label="player.log",
            decoded_text="\n".join(lines),
            used_encoding="utf-8",
            recent_line_count=0,
        )

        report = build_slack_log_report(snapshot)

        self.assertEqual(report.total_lines, 3503)
        self.assertEqual(len(report.summary_lines), 3)
        self.assertEqual(len(report.error_entries), 1)
        self.assertEqual(report.error_entries[0].occurrences, 2)
        self.assertEqual(report.error_entries[0].line_numbers, [3501, 3503])

    def test_format_report_includes_full_sentence_counts_and_anomalies(self) -> None:
        lines = [
            "startup ok",
            "JsonReaderException: Error parsing Infinity value. Path 'banner.value'",
            "[WARN] Retry connecting to gateway",
            "[WARN] Retry connecting to gateway",
        ]
        snapshot = build_log_snapshot_from_text(
            path_label="player.log",
            decoded_text="\n".join(lines),
            used_encoding="utf-8",
            recent_line_count=0,
        )

        result = format_slack_log_report(build_slack_log_report(snapshot))

        self.assertIn("[3줄 요약]", result)
        self.assertIn("발생 1회", result)
        self.assertIn("발생 2회", result)
        self.assertIn("원문: JsonReaderException: Error parsing Infinity value. Path 'banner.value'", result)
        self.assertIn("원문: [WARN] Retry connecting to gateway", result)

    def test_run_analysis_scans_beyond_previous_recent_line_limit(self) -> None:
        lines = [f"heartbeat {index}" for index in range(1, 3201)]
        lines.append("SocketException: Connection timed out while waiting for match server")

        with patch.dict(os.environ, {"SLACK_MASK_SENSITIVE": "0"}, clear=False):
            result = slack_bot._run_analysis_for_text("player.log", "\n".join(lines), "utf-8")

        self.assertIn("전체 검수 라인: 3,201", result)
        self.assertIn("SocketException: Connection timed out while waiting for match server", result)
        self.assertIn("발생 1회", result)

    def test_run_analysis_reports_special_notes_when_no_errors(self) -> None:
        lines = [
            "startup ok",
            "[WARN] Retry connecting to gateway",
            "[WARN] Retry connecting to gateway",
            "session resumed",
        ]

        with patch.dict(os.environ, {"SLACK_MASK_SENSITIVE": "0"}, clear=False):
            result = slack_bot._run_analysis_for_text("player.log", "\n".join(lines), "utf-8")

        self.assertIn("명시적인 오류 구문은 확인되지 않았습니다.", result)
        self.assertIn("[특이사항]", result)
        self.assertIn("Retry connecting to gateway", result)


if __name__ == "__main__":
    unittest.main()
