from __future__ import annotations

import re
from dataclasses import dataclass

from .log_parser import ERROR_PATTERN, LogSnapshot


WARNING_PATTERN = re.compile(
    r"\[WARN(?:ING)?\]|\bWARN(?:ING)?\b|\bRetry(?:ing)?\b|\bReconnect(?:ing)?\b|"
    r"\blatency\b|\bpacket loss\b|\bstall(?:ed|ing)?\b|\bslow(?:ed|ing)?\b|"
    r"\bdelayed?\b|\bdropped\b",
    re.IGNORECASE,
)
HEX_PATTERN = re.compile(r"\b[0-9a-f]{8,64}\b", re.IGNORECASE)
NUMBER_PATTERN = re.compile(r"\d+")
WHITESPACE_PATTERN = re.compile(r"\s+")


@dataclass
class ReportEntry:
    message: str
    occurrences: int
    line_numbers: list[int]
    note: str


@dataclass
class SlackLogReport:
    file_name: str
    total_lines: int
    encoding: str
    error_entries: list[ReportEntry]
    anomaly_entries: list[ReportEntry]
    summary_lines: list[str]

    @property
    def total_error_occurrences(self) -> int:
        return sum(entry.occurrences for entry in self.error_entries)

    @property
    def total_anomaly_occurrences(self) -> int:
        return sum(entry.occurrences for entry in self.anomaly_entries)


def _normalize_signature(line: str) -> str:
    signature = HEX_PATTERN.sub("<hex>", line.strip())
    signature = NUMBER_PATTERN.sub("<n>", signature)
    signature = WHITESPACE_PATTERN.sub(" ", signature)
    return signature[:240] or "unknown"


def _shorten(text: str, limit: int = 88) -> str:
    cleaned = WHITESPACE_PATTERN.sub(" ", text.strip())
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: limit - 3].rstrip() + "..."


def _format_line_numbers(line_numbers: list[int], limit: int = 8) -> str:
    if not line_numbers:
        return "-"
    if len(line_numbers) <= limit:
        return ", ".join(f"{line_no:,}" for line_no in line_numbers)
    preview = ", ".join(f"{line_no:,}" for line_no in line_numbers[: limit - 1])
    return f"{preview}, ... , {line_numbers[-1]:,}"


def _collect_entries(
    lines: list[str],
    *,
    predicate: re.Pattern[str],
    note_builder,
    excluded_lines: set[int] | None = None,
) -> list[ReportEntry]:
    excluded = excluded_lines or set()
    grouped: dict[str, ReportEntry] = {}

    for index, raw_line in enumerate(lines, start=1):
        line = raw_line.strip()
        if not line or index in excluded or not predicate.search(line):
            continue

        signature = _normalize_signature(line)
        entry = grouped.get(signature)
        if entry is None:
            grouped[signature] = ReportEntry(
                message=line,
                occurrences=1,
                line_numbers=[index],
                note=note_builder(line),
            )
            continue

        entry.occurrences += 1
        entry.line_numbers.append(index)

    return sorted(
        grouped.values(),
        key=lambda entry: (-entry.occurrences, entry.line_numbers[0], entry.message.lower()),
    )


def _infer_error_cause(message: str) -> str:
    normalized = message.lower()
    if "nullreferenceexception" in normalized:
        return "객체 참조가 초기화되지 않았거나 해제된 상태에서 접근한 것으로 보입니다."
    if "jsonreaderexception" in normalized or "error parsing infinity value" in normalized:
        return "비정상 JSON 값 또는 손상된 응답 데이터 때문에 파싱에 실패한 것으로 보입니다."
    if "failed to find item by itemcode" in normalized:
        return "클라이언트가 참조한 itemCode 데이터가 누락됐거나 버전이 맞지 않는 것으로 보입니다."
    if "socketexception" in normalized or "disconnect" in normalized or "timed out" in normalized:
        return "네트워크 연결 종료 또는 응답 지연으로 통신이 실패한 것으로 보입니다."
    if "websocket" in normalized and "timeout" in normalized:
        return "실시간 연결이 오래 응답하지 않아 세션이 끊긴 것으로 보입니다."
    if "gpu" in normalized or "d3d" in normalized or "device removed" in normalized:
        return "그래픽 장치 또는 드라이버 상태 문제로 렌더링이 실패한 것으로 보입니다."
    if "unauthorized" in normalized or "forbidden" in normalized or "invalid_auth" in normalized:
        return "인증 정보가 없거나 권한이 맞지 않아 요청이 거부된 것으로 보입니다."
    return "로그 한 줄만으로 단정은 어렵지만, 같은 시점의 직전 동작과 주변 구문을 함께 확인해야 합니다."


def _infer_anomaly_note(message: str) -> str:
    normalized = message.lower()
    if "retry" in normalized or "reconnect" in normalized:
        return "재시도 또는 재접속이 반복돼 네트워크 상태를 확인할 필요가 있습니다."
    if "warn" in normalized or "warning" in normalized:
        return "명시적 오류는 아니지만 상태 이상을 경고하는 메시지입니다."
    if "latency" in normalized or "packet loss" in normalized or "dropped" in normalized:
        return "통신 품질 저하 징후로 보입니다."
    return "오류 전조일 수 있는 이상 징후입니다."


def _build_summary_lines(report: SlackLogReport) -> list[str]:
    if report.error_entries:
        top_error = report.error_entries[0]
        last_error_line = max(
            line_no
            for entry in report.error_entries
            for line_no in entry.line_numbers
        )
        if report.anomaly_entries:
            anomaly_text = (
                f"특이사항은 {len(report.anomaly_entries)}종, 총 {report.total_anomaly_occurrences}건입니다."
            )
        else:
            anomaly_text = "오류 외 추가 특이사항은 눈에 띄지 않았습니다."
        return [
            f"1. 첨부 로그 전체 {report.total_lines:,}줄을 모두 검수했고 오류 {len(report.error_entries)}종, 총 {report.total_error_occurrences}건을 확인했습니다.",
            f"2. 가장 많이 반복된 오류는 \"{_shorten(top_error.message, 72)}\"이며 {top_error.occurrences}회 발생했고, 추정 원인은 {top_error.note}",
            f"3. 마지막 오류는 {last_error_line:,}번째 줄에서 확인됐고 {anomaly_text}",
        ]

    if report.anomaly_entries:
        top_anomaly = report.anomaly_entries[0]
        last_anomaly_line = max(
            line_no
            for entry in report.anomaly_entries
            for line_no in entry.line_numbers
        )
        return [
            f"1. 첨부 로그 전체 {report.total_lines:,}줄을 모두 검수했고 명시적인 오류 구문은 확인되지 않았습니다.",
            f"2. 대신 특이사항 {len(report.anomaly_entries)}종, 총 {report.total_anomaly_occurrences}건을 확인했고 가장 많은 항목은 \"{_shorten(top_anomaly.message, 72)}\"입니다.",
            f"3. 마지막 특이사항은 {last_anomaly_line:,}번째 줄에서 확인됐고, 우선 점검 포인트는 {top_anomaly.note}",
        ]

    return [
        f"1. 첨부 로그 전체 {report.total_lines:,}줄을 모두 검수했고 명시적인 오류 구문은 확인되지 않았습니다.",
        "2. warning, retry, reconnect 계열의 특이사항도 별도로 확인되지 않았습니다.",
        "3. 현재 로그만 기준으로는 재현 가능한 장애보다 정상 종료 또는 일반 동작에 가깝습니다.",
    ]


def build_slack_log_report(snapshot: LogSnapshot) -> SlackLogReport:
    error_line_numbers = {
        index
        for index, line in enumerate(snapshot.full_lines, start=1)
        if line.strip() and ERROR_PATTERN.search(line)
    }
    error_entries = _collect_entries(
        snapshot.full_lines,
        predicate=ERROR_PATTERN,
        note_builder=_infer_error_cause,
    )
    anomaly_entries = _collect_entries(
        snapshot.full_lines,
        predicate=WARNING_PATTERN,
        note_builder=_infer_anomaly_note,
        excluded_lines=error_line_numbers,
    )

    report = SlackLogReport(
        file_name=snapshot.path,
        total_lines=snapshot.total_lines,
        encoding=snapshot.used_encoding,
        error_entries=error_entries,
        anomaly_entries=anomaly_entries,
        summary_lines=[],
    )
    report.summary_lines = _build_summary_lines(report)
    return report


def format_slack_log_report(report: SlackLogReport) -> str:
    lines = [
        "[분석 대상]",
        f"- 파일: {report.file_name}",
        f"- 전체 검수 라인: {report.total_lines:,}",
        f"- 인코딩: {report.encoding}",
        "",
        "[3줄 요약]",
        *report.summary_lines,
        "",
        "[오류 내역]",
    ]

    if report.error_entries:
        for index, entry in enumerate(report.error_entries, start=1):
            lines.append(
                f"{index}. 발생 {entry.occurrences}회 | 추정 원인: {entry.note}"
            )
            lines.append(f"원문: {entry.message}")
            lines.append(f"위치: {_format_line_numbers(entry.line_numbers)}")
    else:
        lines.append("1. 명시적인 error/exception 구문은 확인되지 않았습니다.")

    lines.append("")
    lines.append("[특이사항]")
    if report.anomaly_entries:
        for index, entry in enumerate(report.anomaly_entries, start=1):
            lines.append(
                f"{index}. 발생 {entry.occurrences}회 | 설명: {entry.note}"
            )
            lines.append(f"원문: {entry.message}")
            lines.append(f"위치: {_format_line_numbers(entry.line_numbers)}")
    else:
        lines.append("1. 공유할 만한 warning/retry/reconnect 특이사항은 확인되지 않았습니다.")

    return "\n".join(lines).strip()
