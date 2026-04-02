from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from src import slack_bot
from src.log_parser import build_log_snapshot_from_text
from src.slack_report import build_slack_log_report, format_slack_log_report


class SlackReportTests(unittest.TestCase):
    def test_build_report_groups_related_http_failures(self) -> None:
        lines = [
            "[ERROR][Dev][2026-04-02 15:28:56,996][1][ErHttpRequest:RequestCoroutine:236] Fail Request: One or more errors occurred. (Service Temporarily Unavailable)",
            "[ERROR][Dev][2026-04-02 15:28:56,997][1][ErHttpRequest:OnFailed:475] HandledError: False",
            "[ERROR][Dev][2026-04-02 15:28:56,997][1][ErHttpRequest:OnFailed:486] Result State: Finished | Message:",
            "UnityEngine.Debug:LogError(Object)",
        ]
        snapshot = build_log_snapshot_from_text(
            path_label="player.log",
            decoded_text="\n".join(lines),
            used_encoding="utf-8",
            recent_line_count=0,
        )

        report = build_slack_log_report(snapshot)

        self.assertEqual(len(report.issues), 1)
        self.assertEqual(report.issues[0].title, "서버 API 응답 실패")
        self.assertEqual(report.issues[0].occurrences, 3)
        self.assertIn("Service Temporarily Unavailable", report.issues[0].representative_message())

    def test_build_report_groups_messagepack_deserialization_errors(self) -> None:
        lines = [
            "MessagePackSerializationException: Unexpected msgpack code 145 (fixarray) encountered.",
            "Rethrow as MessagePackSerializationException: Failed to deserialize Blis.Common.CmdKill value.",
            "Rethrow as MessagePackSerializationException: Failed to deserialize Blis.Common.CmdUpdateMoveSpeed value.",
        ]
        snapshot = build_log_snapshot_from_text(
            path_label="player.log",
            decoded_text="\n".join(lines),
            used_encoding="utf-8",
            recent_line_count=0,
        )

        report = build_slack_log_report(snapshot)

        self.assertEqual(len(report.issues), 1)
        self.assertEqual(report.issues[0].title, "네트워크 동기화 데이터 해석 실패")
        self.assertEqual(report.issues[0].occurrences, 3)
        self.assertIn("Failed to deserialize Blis.Common", report.issues[0].representative_message())

    def test_build_report_groups_object_sync_errors(self) -> None:
        lines = [
            "[ERROR][DEV][11:43:26.445][1][GameClient:HandleCommand:1108] Failed to find object by ObjectId[3396] ObjectType[Blis.Client.LocalMonster]",
            "[ERROR][DEV][11:54:19.084][1][LocalWorld:CreateObject:131] ResourceItemBox ObjectId[1108] is duplicated.",
            "[ERROR][DEV][11:54:19.084][1][LocalProjectilePoolService:ReturnPool:103] [LocalProjectilePoolService.ReturnPool] no key : 1857",
        ]
        snapshot = build_log_snapshot_from_text(
            path_label="player.log",
            decoded_text="\n".join(lines),
            used_encoding="utf-8",
            recent_line_count=0,
        )

        report = build_slack_log_report(snapshot)

        self.assertEqual(len(report.issues), 1)
        self.assertEqual(report.issues[0].title, "오브젝트 동기화 누락 또는 중복 생성")
        self.assertEqual(report.issues[0].occurrences, 3)

    def test_handle_command_wrapper_is_treated_as_noise(self) -> None:
        lines = [
            "[ERROR][DEV][11:43:26.713][1][GameClient:HandleCommand:1107] Exception occurred while HandleCommand: CmdFinishSkill",
            "NullReferenceException: Object reference not set to an instance of an object.",
        ]
        snapshot = build_log_snapshot_from_text(
            path_label="player.log",
            decoded_text="\n".join(lines),
            used_encoding="utf-8",
            recent_line_count=0,
        )

        report = build_slack_log_report(snapshot)

        self.assertEqual(len(report.issues), 1)
        self.assertEqual(report.issues[0].title, "클라이언트 내부 참조 오류")
        self.assertEqual(report.issues[0].occurrences, 1)

    def test_format_report_hides_warning_section_when_errors_exist(self) -> None:
        lines = [
            "NullReferenceException: Object reference not set to an instance of an object.",
            "[WARN] Retry connecting to gateway",
        ]
        snapshot = build_log_snapshot_from_text(
            path_label="player.log",
            decoded_text="\n".join(lines),
            used_encoding="utf-8",
            recent_line_count=0,
        )

        result = format_slack_log_report(build_slack_log_report(snapshot))

        self.assertIn("[핵심 요약]", result)
        self.assertIn("[주요 오류]", result)
        self.assertNotIn("[참고 특이사항]", result)
        self.assertNotIn("Retry connecting to gateway", result)

    def test_run_analysis_scans_entire_file_and_summarizes_by_issue(self) -> None:
        lines = [f"heartbeat {index}" for index in range(1, 3201)]
        lines.append(
            "[ERROR][Dev][2026-04-02 15:28:56,996][1][ErHttpRequest:RequestCoroutine:236] Fail Request: One or more errors occurred. (Service Temporarily Unavailable)"
        )
        lines.append("NullReferenceException: Object reference not set to an instance of an object.")

        with patch.dict(os.environ, {"SLACK_MASK_SENSITIVE": "0"}, clear=False):
            result = slack_bot._run_analysis_for_text("player.log", "\n".join(lines), "utf-8")

        self.assertIn("전체 검수 라인: 3,202", result)
        self.assertIn("서버 API 응답 실패", result)
        self.assertIn("클라이언트 내부 참조 오류", result)
        self.assertIn("- 위치: 3,201줄", result)

    def test_no_error_report_includes_compact_special_notes(self) -> None:
        lines = [
            "startup ok",
            "[WARN] Retry connecting to gateway",
            "[WARN] Retry connecting to gateway",
            "session resumed",
        ]

        with patch.dict(os.environ, {"SLACK_MASK_SENSITIVE": "0"}, clear=False):
            result = slack_bot._run_analysis_for_text("player.log", "\n".join(lines), "utf-8")

        self.assertIn("명시적인 error/exception 구문은 확인되지 않았습니다.", result)
        self.assertIn("[참고 특이사항]", result)
        self.assertIn("Retry connecting to gateway", result)


if __name__ == "__main__":
    unittest.main()
