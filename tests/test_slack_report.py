from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from src import slack_bot
from src.log_parser import build_log_snapshot_from_text
from src.slack_report import build_slack_log_report, format_slack_log_report


class SlackReportTests(unittest.TestCase):
    def _build_snapshot(self, *lines: str):
        return build_log_snapshot_from_text(
            path_label="player.log",
            decoded_text="\n".join(lines),
            used_encoding="utf-8",
            recent_line_count=0,
        )

    def test_build_report_groups_related_http_failures(self) -> None:
        snapshot = self._build_snapshot(
            "[ERROR][Dev][2026-04-02 15:28:56,996][1][ErHttpRequest:RequestCoroutine:236] Fail Request: One or more errors occurred. (Service Temporarily Unavailable)",
            "[ERROR][Dev][2026-04-02 15:28:56,997][1][ErHttpRequest:OnFailed:475] HandledError: False",
            "[ERROR][Dev][2026-04-02 15:28:56,997][1][ErHttpRequest:OnFailed:486] Result State: Finished | Message:",
            "UnityEngine.Debug:LogError(Object)",
        )

        report = build_slack_log_report(snapshot)

        self.assertEqual(len(report.issues), 1)
        self.assertEqual(report.issues[0].key, "server_api_failure")
        self.assertEqual(report.issues[0].occurrences, 3)
        self.assertIn("Service Temporarily Unavailable", report.issues[0].representative_message())

    def test_build_report_groups_messagepack_deserialization_errors(self) -> None:
        snapshot = self._build_snapshot(
            "MessagePackSerializationException: Unexpected msgpack code 145 (fixarray) encountered.",
            "Rethrow as MessagePackSerializationException: Failed to deserialize Blis.Common.CmdKill value.",
            "Rethrow as MessagePackSerializationException: Failed to deserialize Blis.Common.CmdUpdateMoveSpeed value.",
        )

        report = build_slack_log_report(snapshot)

        self.assertEqual(len(report.issues), 1)
        self.assertEqual(report.issues[0].key, "messagepack_deserialization_failure")
        self.assertEqual(report.issues[0].occurrences, 3)
        self.assertIn("Failed to deserialize Blis.Common", report.issues[0].representative_message())

    def test_build_report_groups_object_sync_errors(self) -> None:
        snapshot = self._build_snapshot(
            "[ERROR][DEV][11:43:26.445][1][GameClient:HandleCommand:1108] Failed to find object by ObjectId[3396] ObjectType[Blis.Client.LocalMonster]",
            "[ERROR][DEV][11:54:19.084][1][LocalWorld:CreateObject:131] ResourceItemBox ObjectId[1108] is duplicated.",
            "[ERROR][DEV][11:54:19.084][1][LocalProjectilePoolService:ReturnPool:103] [LocalProjectilePoolService.ReturnPool] no key : 1857",
        )

        report = build_slack_log_report(snapshot)

        self.assertEqual(len(report.issues), 1)
        self.assertEqual(report.issues[0].key, "object_sync_failure")
        self.assertEqual(report.issues[0].occurrences, 3)

    def test_build_report_groups_team_sight_errors_as_object_sync(self) -> None:
        snapshot = self._build_snapshot(
            "[ERROR][DEV][2026-03-06 18:13:55,746][1][TeamSightManager:IsInTeamViewableList:552] [IsInTeamViewableList] mySightAgent is null : PlayerCharacter, NotInWorld, 1338",
            "[ERROR][DEV][2026-03-06 17:58:19,758][1][TeamSightManager:SetAliveSight:138] [TeamSightManager.SetAliveSight] Not Exist Main sight : MedicBot",
        )

        report = build_slack_log_report(snapshot)

        self.assertEqual(len(report.issues), 1)
        self.assertEqual(report.issues[0].key, "object_sync_failure")
        self.assertEqual(report.issues[0].occurrences, 2)

    def test_build_report_groups_item_and_resource_lookup_as_data_mismatch(self) -> None:
        snapshot = self._build_snapshot(
            "[ERROR][Dev][2026-03-30 10:52:45,941][1][ItemDB:FindItemByCode:105] Failed to find item by itemCode[0]",
            "[ERROR][Dev][2026-03-26 15:43:04,529][1][WeaponMountController:UpdateWeaponAnimation:196] WeaponMount not Find Craft_Tool_Metal_01 / M_Tool",
            "[ERROR][Dev][2026-03-30 10:52:44,704][1][StartPositionService:AssignmentAlgorithm:125] missing spawn point for area code. currentLevel path : Default / areaCode : 3011060",
        )

        report = build_slack_log_report(snapshot)

        self.assertEqual(len(report.issues), 1)
        self.assertEqual(report.issues[0].key, "game_data_mismatch")
        self.assertEqual(report.issues[0].occurrences, 3)

    def test_build_report_groups_handle_command_timeout_as_socket_issue(self) -> None:
        snapshot = self._build_snapshot(
            "[ERROR][MINI-PC][2026-03-24 19:53:59,755][1][GameClient:FrameUpdate:1119] [GameClient] connection closed: HANDLE_COMMAND_TIME_OUT",
        )

        report = build_slack_log_report(snapshot)

        self.assertEqual(len(report.issues), 1)
        self.assertEqual(report.issues[0].key, "socket_connection_issue")
        self.assertIn("HANDLE_COMMAND_TIME_OUT", report.issues[0].representative_message())

    def test_build_report_groups_raw_input_failure(self) -> None:
        snapshot = self._build_snapshot(
            "<Raw Input> Failed to get raw input data: invalid handle",
            "<Raw Input> Failed to get raw input data: invalid handle",
        )

        report = build_slack_log_report(snapshot)

        self.assertEqual(len(report.issues), 1)
        self.assertEqual(report.issues[0].key, "input_device_failure")
        self.assertEqual(report.issues[0].occurrences, 2)

    def test_handle_command_wrapper_is_treated_as_noise(self) -> None:
        snapshot = self._build_snapshot(
            "[ERROR][DEV][11:43:26.713][1][GameClient:HandleCommand:1107] Exception occurred while HandleCommand: CmdFinishSkill",
            "NullReferenceException: Object reference not set to an instance of an object.",
        )

        report = build_slack_log_report(snapshot)

        self.assertEqual(len(report.issues), 1)
        self.assertEqual(report.issues[0].key, "null_reference")
        self.assertEqual(report.issues[0].occurrences, 1)

    def test_build_report_treats_stack_frames_as_noise(self) -> None:
        snapshot = self._build_snapshot(
            "NullReferenceException: Object reference not set to an instance of an object.",
            "at System.Runtime.CompilerServices.AsyncTaskMethodBuilder`1[TResult].SetException (System.Exception exception) [0x00000] in <00000000000000000000000000000000>:0",
            "--- End of stack trace from previous location where exception was thrown ---",
            "Cysharp.Threading.Tasks.UniTaskCompletionSourceCore`1:TrySetException(Exception)",
        )

        report = build_slack_log_report(snapshot)

        self.assertEqual(len(report.issues), 1)
        self.assertEqual(report.issues[0].key, "null_reference")
        self.assertEqual(report.issues[0].occurrences, 1)

    def test_format_report_hides_warning_section_when_errors_exist(self) -> None:
        snapshot = self._build_snapshot(
            "NullReferenceException: Object reference not set to an instance of an object.",
            "[WARN] Retry connecting to gateway",
        )

        result = format_slack_log_report(build_slack_log_report(snapshot))

        self.assertIn("[", result)
        self.assertNotIn("Retry connecting to gateway", result)

    def test_run_analysis_scans_entire_file_and_summarizes_by_issue(self) -> None:
        lines = [f"heartbeat {index}" for index in range(1, 3201)]
        lines.append(
            "[ERROR][Dev][2026-04-02 15:28:56,996][1][ErHttpRequest:RequestCoroutine:236] Fail Request: One or more errors occurred. (Service Temporarily Unavailable)"
        )
        lines.append("NullReferenceException: Object reference not set to an instance of an object.")

        with patch.dict(os.environ, {"SLACK_MASK_SENSITIVE": "0"}, clear=False):
            result = slack_bot._run_analysis_for_text("player.log", "\n".join(lines), "utf-8")

        self.assertIn("3,202", result)
        self.assertIn("Service Temporarily Unavailable", result)
        self.assertIn("NullReferenceException", result)

    def test_no_error_report_includes_compact_special_notes(self) -> None:
        with patch.dict(os.environ, {"SLACK_MASK_SENSITIVE": "0"}, clear=False):
            result = slack_bot._run_analysis_for_text(
                "player.log",
                "\n".join(
                    [
                        "startup ok",
                        "[WARN] Retry connecting to gateway",
                        "[WARN] Retry connecting to gateway",
                        "session resumed",
                    ]
                ),
                "utf-8",
            )

        self.assertIn("Retry connecting to gateway", result)


if __name__ == "__main__":
    unittest.main()
