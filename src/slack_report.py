from __future__ import annotations

import re
from dataclasses import dataclass, field

from .log_parser import ERROR_PATTERN, LogSnapshot


SPECIAL_NOTE_PATTERN = re.compile(
    r"\[WARN(?:ING)?\]|\bWARN(?:ING)?\b|\bRetry(?:ing)?\b|\bReconnect(?:ing)?\b|"
    r"\blatency\b|\bpacket loss\b|\bdropped\b|\bstalled?\b|\bdelay(?:ed)?\b",
    re.IGNORECASE,
)
URL_PATTERN = re.compile(r"https?://\S+", re.IGNORECASE)
HEX_PATTERN = re.compile(r"\b[0-9a-f]{8,64}\b", re.IGNORECASE)
NUMBER_PATTERN = re.compile(r"\d+")
WHITESPACE_PATTERN = re.compile(r"\s+")
MAX_ISSUES = 5
MAX_SPECIAL_NOTES = 3

NOISE_PATTERNS = [
    re.compile(r"^UnityEngine\.(Debug|Logger):", re.IGNORECASE),
    re.compile(r"^Sentry\.", re.IGNORECASE),
    re.compile(r"^Sentry:", re.IGNORECASE),
    re.compile(r"^Blis\.Common\.", re.IGNORECASE),
    re.compile(r"DiagnosticLoggerExtensions", re.IGNORECASE),
    re.compile(r"^Uploading Crash Report$", re.IGNORECASE),
    re.compile(r"^BestHTTP\.SocketIO\.Socket:BestHTTP\.SocketIO\.ISocket\.Disconnect", re.IGNORECASE),
]


@dataclass(frozen=True)
class IssueRule:
    key: str
    title: str
    impact: str
    cause: str
    patterns: tuple[re.Pattern[str], ...]
    representative_patterns: tuple[re.Pattern[str], ...] = ()


@dataclass
class ReportIssue:
    key: str
    title: str
    impact: str
    cause: str
    occurrences: int = 0
    line_numbers: list[int] = field(default_factory=list)
    matched_lines: list[str] = field(default_factory=list)

    def representative_message(self) -> str:
        if not self.matched_lines:
            return self.title
        return self.matched_lines[0]

    @property
    def first_line(self) -> int:
        return self.line_numbers[0]

    @property
    def last_line(self) -> int:
        return self.line_numbers[-1]


@dataclass
class SpecialNote:
    message: str
    occurrences: int
    line_numbers: list[int]
    note: str

    @property
    def first_line(self) -> int:
        return self.line_numbers[0]

    @property
    def last_line(self) -> int:
        return self.line_numbers[-1]


@dataclass
class SlackLogReport:
    file_name: str
    total_lines: int
    encoding: str
    issues: list[ReportIssue]
    hidden_issue_count: int
    hidden_issue_occurrences: int
    summary_lines: list[str]
    special_notes: list[SpecialNote]

    @property
    def total_issue_occurrences(self) -> int:
        return sum(issue.occurrences for issue in self.issues) + self.hidden_issue_occurrences


ISSUE_RULES = [
    IssueRule(
        key="server_api_failure",
        title="서버 API 응답 실패",
        impact="서버에서 받아와야 할 데이터를 가져오지 못해 여러 기능 로딩이 연쇄적으로 실패한 것으로 보입니다.",
        cause="서버가 일시적으로 정상 응답을 주지 않았거나, 요청 처리 중 장애가 있었을 가능성이 큽니다.",
        patterns=(
            re.compile(r"Service Temporarily Unavailable", re.IGNORECASE),
            re.compile(r"Fail Request:", re.IGNORECASE),
            re.compile(r"ErHttpRequest:OnFailed", re.IGNORECASE),
            re.compile(r"HttpFinished", re.IGNORECASE),
            re.compile(r"RequestCoroutine", re.IGNORECASE),
        ),
        representative_patterns=(
            re.compile(r"Service Temporarily Unavailable", re.IGNORECASE),
            re.compile(r"Fail Request:", re.IGNORECASE),
        ),
    ),
    IssueRule(
        key="response_parse_failure",
        title="서버 응답 데이터 해석 실패",
        impact="받아온 데이터 형식이 예상과 달라 일부 화면 정보나 공지 데이터를 열지 못한 것으로 보입니다.",
        cause="서버가 비정상 JSON 또는 손상된 값을 반환해 클라이언트 파싱이 실패한 것으로 보입니다.",
        patterns=(
            re.compile(r"JsonReaderException", re.IGNORECASE),
            re.compile(r"Unexpected character encountered while parsing value", re.IGNORECASE),
            re.compile(r"Error parsing Infinity value", re.IGNORECASE),
            re.compile(r"Failed to load version data", re.IGNORECASE),
        ),
        representative_patterns=(re.compile(r"JsonReaderException", re.IGNORECASE),),
    ),
    IssueRule(
        key="null_reference",
        title="클라이언트 내부 참조 오류",
        impact="필요한 객체나 값이 비어 있는 상태에서 접근해 일부 기능이 비정상 종료된 것으로 보입니다.",
        cause="서버 응답 누락이나 초기화 순서 문제로 필요한 객체가 준비되지 않은 상태였을 가능성이 큽니다.",
        patterns=(re.compile(r"NullReferenceException", re.IGNORECASE),),
        representative_patterns=(re.compile(r"NullReferenceException", re.IGNORECASE),),
    ),
    IssueRule(
        key="socket_connection_issue",
        title="실시간 연결 끊김 또는 시간 초과",
        impact="실시간 통신이 끊겨 세션 유지나 채널 연결이 불안정했던 것으로 보입니다.",
        cause="네트워크 지연, 서버 응답 지연, 또는 소켓 세션 종료가 겹쳤을 가능성이 큽니다.",
        patterns=(
            re.compile(r"WebSocket", re.IGNORECASE),
            re.compile(r"Socket", re.IGNORECASE),
            re.compile(r"SOCKET DISCONNECTED", re.IGNORECASE),
            re.compile(r"timed out", re.IGNORECASE),
            re.compile(r"\bdisconnect(?:ed|ion)?\b", re.IGNORECASE),
        ),
        representative_patterns=(
            re.compile(r"SOCKET DISCONNECTED", re.IGNORECASE),
            re.compile(r"WebSocket", re.IGNORECASE),
            re.compile(r"timed out", re.IGNORECASE),
        ),
    ),
    IssueRule(
        key="gpu_render_issue",
        title="그래픽 장치 또는 렌더링 문제",
        impact="그래픽 처리나 화면 출력이 정상적으로 진행되지 않았을 가능성이 있습니다.",
        cause="GPU 장치 상태나 드라이버와의 통신 문제일 수 있습니다.",
        patterns=(
            re.compile(r"\bD3D\b", re.IGNORECASE),
            re.compile(r"\bGPU\b", re.IGNORECASE),
            re.compile(r"device removed", re.IGNORECASE),
        ),
    ),
]


def _normalize_signature(line: str) -> str:
    normalized = URL_PATTERN.sub("<url>", line.strip())
    normalized = HEX_PATTERN.sub("<hex>", normalized)
    normalized = NUMBER_PATTERN.sub("<n>", normalized)
    normalized = WHITESPACE_PATTERN.sub(" ", normalized)
    return normalized[:240] or "unknown"


def _shorten(text: str, limit: int = 84) -> str:
    cleaned = WHITESPACE_PATTERN.sub(" ", text.strip())
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: limit - 3].rstrip() + "..."


def _format_line_range(first_line: int, last_line: int) -> str:
    if first_line == last_line:
        return f"{first_line:,}줄"
    return f"첫 발생 {first_line:,}줄 / 마지막 {last_line:,}줄"


def _is_noise_line(line: str) -> bool:
    stripped = line.strip()
    if not stripped:
        return True
    return any(pattern.search(stripped) for pattern in NOISE_PATTERNS)


def _match_rule(line: str) -> IssueRule | None:
    for rule in ISSUE_RULES:
        if any(pattern.search(line) for pattern in rule.patterns):
            return rule
    return None


def _pick_representative_line(rule: IssueRule | None, lines: list[str]) -> str:
    if not lines:
        return ""
    if rule is not None:
        for pattern in rule.representative_patterns:
            for line in lines:
                if pattern.search(line):
                    return line
    return max(lines, key=lambda value: (len(value), value))


def _build_generic_issue(message: str) -> tuple[str, str, str]:
    lowered = message.lower()
    if "exception" in lowered:
        return (
            "기타 예외 발생",
            "명시적인 예외가 기록됐지만, 현재 규칙으로는 세부 분류가 어렵습니다.",
            "대표 예외 문장과 직전 동작을 함께 확인해야 정확한 원인 판단이 가능합니다.",
        )
    return (
        "기타 오류 발생",
        "오류 로그는 확인됐지만 대표 패턴으로 묶이지 않는 항목입니다.",
        "동일 시점의 전후 로그를 함께 봐야 원인 판단이 가능합니다.",
    )


def _collect_issues(lines: list[str]) -> list[ReportIssue]:
    grouped: dict[str, ReportIssue] = {}

    for line_number, raw_line in enumerate(lines, start=1):
        line = raw_line.strip()
        if not line or not ERROR_PATTERN.search(line) or _is_noise_line(line):
            continue

        rule = _match_rule(line)
        if rule is None:
            title, impact, cause = _build_generic_issue(line)
            key = f"generic:{_normalize_signature(line)}"
            issue = grouped.get(key)
            if issue is None:
                issue = ReportIssue(key=key, title=title, impact=impact, cause=cause)
                grouped[key] = issue
        else:
            key = rule.key
            issue = grouped.get(key)
            if issue is None:
                issue = ReportIssue(
                    key=key,
                    title=rule.title,
                    impact=rule.impact,
                    cause=rule.cause,
                )
                grouped[key] = issue

        issue.occurrences += 1
        issue.line_numbers.append(line_number)
        issue.matched_lines.append(line)

    issues = list(grouped.values())
    for issue in issues:
        rule = next((candidate for candidate in ISSUE_RULES if candidate.key == issue.key), None)
        representative = _pick_representative_line(rule, issue.matched_lines)
        issue.matched_lines = [representative]

    return sorted(
        issues,
        key=lambda issue: (-issue.occurrences, issue.first_line, issue.title.lower()),
    )


def _infer_special_note(message: str) -> str:
    lowered = message.lower()
    if "retry" in lowered or "reconnect" in lowered:
        return "재시도 또는 재접속이 반복돼 연결 상태를 확인할 필요가 있습니다."
    if "latency" in lowered or "packet loss" in lowered or "dropped" in lowered:
        return "통신 품질 저하 징후로 보입니다."
    return "명시적 오류는 아니지만 상태 이상 징후가 보입니다."


def _collect_special_notes(lines: list[str]) -> list[SpecialNote]:
    grouped: dict[str, SpecialNote] = {}

    for line_number, raw_line in enumerate(lines, start=1):
        line = raw_line.strip()
        if not line or not SPECIAL_NOTE_PATTERN.search(line):
            continue
        key = _normalize_signature(line)
        note = grouped.get(key)
        if note is None:
            grouped[key] = SpecialNote(
                message=line,
                occurrences=1,
                line_numbers=[line_number],
                note=_infer_special_note(line),
            )
            continue
        note.occurrences += 1
        note.line_numbers.append(line_number)

    return sorted(
        grouped.values(),
        key=lambda note: (-note.occurrences, note.first_line, note.message.lower()),
    )


def _build_summary_lines(
    total_lines: int,
    visible_issues: list[ReportIssue],
    total_issue_count: int,
    total_issue_occurrences: int,
    hidden_issue_count: int,
) -> list[str]:
    if not visible_issues:
        return [
            "1. 첨부 로그 전체를 검수했지만 명시적인 error/exception 구문은 확인되지 않았습니다.",
            "2. 이번 보고서는 에러 중심으로 압축했기 때문에 경고성 메시지는 핵심 판단에서 제외했습니다.",
            "3. 오류가 재현된다면 같은 시점의 더 긴 로그를 다시 확보하는 것이 좋습니다.",
        ]

    top_issue = visible_issues[0]
    if len(visible_issues) > 1:
        followups = ", ".join(issue.title for issue in visible_issues[1:3])
        followup_text = f"후속 영향으로 {followups}도 함께 보입니다."
    else:
        followup_text = "동급의 다른 독립 오류는 두드러지지 않습니다."

    if hidden_issue_count > 0:
        hidden_text = f" 나머지 {hidden_issue_count}종은 중요도가 낮아 생략했습니다."
    else:
        hidden_text = ""

    return [
        f"1. 첨부 로그 전체 {total_lines:,}줄을 검수했고, 실제 원인으로 묶인 주요 오류는 {total_issue_count}종, 총 {total_issue_occurrences}건입니다.",
        f"2. 가장 큰 문제는 {top_issue.title}이며 {top_issue.occurrences}회 확인됐습니다. {top_issue.impact}",
        f"3. {followup_text}{hidden_text}",
    ]


def build_slack_log_report(snapshot: LogSnapshot) -> SlackLogReport:
    all_issues = _collect_issues(snapshot.full_lines)
    visible_issues = all_issues[:MAX_ISSUES]
    hidden_issues = all_issues[MAX_ISSUES:]
    hidden_issue_occurrences = sum(issue.occurrences for issue in hidden_issues)
    special_notes = _collect_special_notes(snapshot.full_lines)[:MAX_SPECIAL_NOTES] if not all_issues else []

    summary_lines = _build_summary_lines(
        snapshot.total_lines,
        visible_issues,
        total_issue_count=len(all_issues),
        total_issue_occurrences=sum(issue.occurrences for issue in all_issues),
        hidden_issue_count=len(hidden_issues),
    )

    return SlackLogReport(
        file_name=snapshot.path,
        total_lines=snapshot.total_lines,
        encoding=snapshot.used_encoding,
        issues=visible_issues,
        hidden_issue_count=len(hidden_issues),
        hidden_issue_occurrences=hidden_issue_occurrences,
        summary_lines=summary_lines,
        special_notes=special_notes,
    )


def format_slack_log_report(report: SlackLogReport) -> str:
    lines = [
        "[분석 대상]",
        f"- 파일: {report.file_name}",
        f"- 전체 검수 라인: {report.total_lines:,}",
        f"- 인코딩: {report.encoding}",
        "",
        "[핵심 요약]",
        *report.summary_lines,
        "",
    ]

    if report.issues:
        lines.append("[주요 오류]")
        for index, issue in enumerate(report.issues, start=1):
            lines.append(f"{index}. {issue.title}")
            lines.append(f"- 의미: {issue.impact}")
            lines.append(f"- 발생: {issue.occurrences}회")
            lines.append(f"- 추정 원인: {issue.cause}")
            lines.append(f"- 대표 원문: {issue.representative_message()}")
            lines.append(f"- 위치: {_format_line_range(issue.first_line, issue.last_line)}")
            lines.append("")

        if report.hidden_issue_count > 0:
            lines.append("[생략된 기타 오류]")
            lines.append(
                f"- 나머지 {report.hidden_issue_count}종, {report.hidden_issue_occurrences}건은 대표성이 낮아 요약에서 제외했습니다."
            )
    else:
        lines.append("[주요 오류]")
        lines.append("- 확인된 명시적 오류는 없습니다.")
        if report.special_notes:
            lines.append("")
            lines.append("[참고 특이사항]")
            for index, note in enumerate(report.special_notes, start=1):
                lines.append(f"{index}. {_shorten(note.message, 100)}")
                lines.append(f"- 발생: {note.occurrences}회")
                lines.append(f"- 설명: {note.note}")
                lines.append(f"- 위치: {_format_line_range(note.first_line, note.last_line)}")
                lines.append("")

    return "\n".join(lines).strip()
