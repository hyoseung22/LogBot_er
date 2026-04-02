from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from .log_parser import ErrorBlock, LogSnapshot


@dataclass
class AnalysisResult:
    summary: str
    causes: list[str]
    actions: list[str]
    followups: list[str]
    evidence: list[str]
    confidence: str
    source: str
    meta_lines: list[str]
    ai_comment: str | None = None
    ai_status: str | None = None


def _append_unique(target: list[str], values: list[str]) -> None:
    for value in values:
        if value not in target:
            target.append(value)


def build_local_analysis(snapshot: LogSnapshot, blocks: list[ErrorBlock]) -> AnalysisResult:
    causes: list[str] = []
    actions: list[str] = []
    followups: list[str] = []
    evidence: list[str] = []
    confidence = "낮음"

    joined = "\n".join(block.text for block in blocks)

    if "NullReferenceException" in joined:
        _append_unique(
            causes,
            [
                "A client-side object reference likely became invalid, which triggered a null reference exception.",
                "클라이언트 객체 참조가 비정상 상태가 되어 NullReferenceException이 발생했을 가능성이 높습니다.",
                "화면 전환, 리소스 로딩, 아이템 데이터 상태와 연결됐을 수 있습니다.",
            ],
        )
        _append_unique(
            actions,
            [
                "동일 증상을 다시 재현해 보고, 발생 직전 행동을 함께 기록하세요.",
                "로컬 캐시나 설정을 초기화한 뒤 다시 시도하세요.",
                "같은 빌드에서 반복되면 원본 로그와 함께 전달하세요.",
            ],
        )
        _append_unique(
            followups,
            [
                "예외가 발생하기 직전 어떤 화면 또는 동작이 있었는지 확인이 필요합니다.",
                "최근 패치 이후 시작된 문제인지 함께 확인해야 합니다.",
            ],
        )
        confidence = "중간"

    if "JsonReaderException" in joined or "Error parsing Infinity value" in joined:
        _append_unique(
            causes,
            [
                "JSON 데이터에 Infinity 값 또는 비정상 포맷이 포함되어 파싱에 실패한 것으로 보입니다.",
                "배너나 이벤트성 데이터 로딩 중 발생했을 수 있습니다.",
            ],
        )
        _append_unique(
            actions,
            [
                "클라이언트가 최신 상태인지 확인한 뒤 다시 시도하세요.",
                "배너나 이벤트 갱신 시점과 겹친다면 서버 데이터 이상 가능성도 함께 확인해야 합니다.",
            ],
        )
        confidence = "중간"

    if "Failed to find item by itemCode" in joined:
        _append_unique(
            causes,
            [
                "클라이언트가 itemCode를 찾지 못하고 있어 잘못된 식별자 또는 누락된 테이블 데이터 가능성이 있습니다.",
                "이 오류가 뒤따르는 예외의 선행 원인일 수 있습니다.",
            ],
        )
        _append_unique(
            actions,
            [
                "문제 발생 당시의 모드, 캐릭터, 인벤토리 상태를 같이 기록하세요.",
                "클라이언트 데이터 갱신 또는 재설치가 필요한지 확인하세요.",
            ],
        )
        confidence = "중간"

    if "WebSocket" in joined and "Timeout" in joined:
        _append_unique(
            causes,
            [
                "소켓 연결 또는 핸드셰이크 지연이 있었을 가능성이 높습니다.",
                "일시적인 네트워크 불안정이나 서버 응답 지연일 수 있습니다.",
            ],
        )
        _append_unique(
            actions,
            [
                "VPN, 프록시, 방화벽 간섭 여부를 확인하세요.",
                "다른 네트워크에서 다시 시도해 환경 문제인지 확인하세요.",
            ],
        )
        confidence = "중간"

    if "Disconnect" in joined and not causes:
        _append_unique(
            causes,
            [
                "현재 보이는 대표 패턴은 소켓 연결 종료 흐름입니다.",
                "정상 종료 과정일 수도 있고, 앞선 오류 이후의 후속 현상일 수도 있습니다.",
            ],
        )
        _append_unique(
            actions,
            [
                "연결 종료 시점과 실제 사용자 행동을 비교해 보세요.",
                "같은 로그 안에 더 이른 시점의 다른 오류가 있는지 확인하세요.",
            ],
        )

    if not causes:
        causes.append(
            "대표 원인을 하나로 단정할 만큼 충분한 단서가 없습니다. 추출된 오류 블록을 추가 확인해야 합니다."
        )
        actions.extend(
            [
                "문제를 재현한 직후 로그를 다시 수집하세요.",
                "문제 직전 행동과 로그 내용을 함께 비교해 보세요.",
            ]
        )
        followups.append(
            "실패 시각과 직전 행동을 함께 확보하면 분류 정확도가 올라갑니다."
        )

    for block in blocks[:6]:
        evidence.append(
            f"[{block.start_line}-{block.end_line}] {block.trigger_line} (반복 {block.occurrences}회)"
        )

    if len(blocks) >= 3 and confidence == "낮음":
        confidence = "중간"
    if "NullReferenceException" in joined and "Failed to find item by itemCode" in joined:
        confidence = "중간~높음"

    summary = (
        f"로그에서 {len(blocks)}개의 오류 블록을 추출했습니다. "
        f"현재 기준으로 가장 가능성 높은 해석은 다음과 같습니다: {causes[0]}"
    )

    meta_lines = [
        f"분석 시각: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"대상 파일: {snapshot.path}",
        f"파일 크기: {snapshot.file_size:,} bytes",
        f"전체 줄 수: {snapshot.total_lines:,}",
        f"분석한 줄 수: {snapshot.loaded_lines:,}",
        f"인코딩: {snapshot.used_encoding}",
        f"추출 블록 수: {len(blocks)}",
    ]
    if snapshot.note:
        meta_lines.append(f"참고: {snapshot.note}")

    return AnalysisResult(
        summary=summary,
        causes=causes,
        actions=actions,
        followups=followups,
        evidence=evidence,
        confidence=confidence,
        source="local",
        meta_lines=meta_lines,
    )
