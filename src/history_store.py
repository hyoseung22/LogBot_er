from __future__ import annotations

import json
import sys
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

from .heuristics import AnalysisResult
from .log_parser import ErrorBlock



def _base_data_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent / "data"
    return Path(__file__).resolve().parents[1] / "data"


HISTORY_PATH = _base_data_dir() / "history.jsonl"


@dataclass
class AnalysisHistoryRecord:
    timestamp: str
    log_path: str
    analysis_mode: str
    summary: str
    ai_comment: str | None
    confidence: str
    block_signatures: list[str]
    trigger_lines: list[str]
    causes: list[str]


def build_record(
    *,
    log_path: str,
    analysis_mode: str,
    result: AnalysisResult,
    blocks: list[ErrorBlock],
) -> AnalysisHistoryRecord:
    return AnalysisHistoryRecord(
        timestamp=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        log_path=log_path,
        analysis_mode=analysis_mode,
        summary=result.summary,
        ai_comment=result.ai_comment,
        confidence=result.confidence,
        block_signatures=[block.signature for block in blocks[:8]],
        trigger_lines=[block.trigger_line for block in blocks[:8]],
        causes=result.causes[:6],
    )


def append_history(record: AnalysisHistoryRecord) -> None:
    HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
    with HISTORY_PATH.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(asdict(record), ensure_ascii=False) + "\n")


def load_history(limit: int = 200) -> list[AnalysisHistoryRecord]:
    if not HISTORY_PATH.exists():
        return []

    records: list[AnalysisHistoryRecord] = []
    with HISTORY_PATH.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(payload, dict):
                continue
            try:
                records.append(AnalysisHistoryRecord(**payload))
            except TypeError:
                continue
    return records[-limit:]


def find_similar_history(blocks: list[ErrorBlock], limit: int = 3) -> list[AnalysisHistoryRecord]:
    if not blocks:
        return []

    current_signatures = {block.signature for block in blocks[:8]}
    if not current_signatures:
        return []

    scored: list[tuple[int, AnalysisHistoryRecord]] = []
    for record in reversed(load_history()):
        record_signatures = set(record.block_signatures)
        overlap = len(current_signatures & record_signatures)
        if overlap <= 0:
            continue
        scored.append((overlap, record))

    scored.sort(key=lambda item: item[0], reverse=True)
    return [record for _, record in scored[:limit]]


def format_history_summary(records: list[AnalysisHistoryRecord]) -> str:
    if not records:
        return "이전 유사 로그 기록이 없습니다. 이번 분석부터 누적 저장됩니다."

    lines = [f"이전 유사 로그 {len(records)}건을 찾았습니다."]
    for index, record in enumerate(records, start=1):
        lines.append(
            f"{index}. {record.timestamp} | {record.analysis_mode} | {record.confidence}"
        )
        lines.append(f"   요약: {record.summary}")
        if record.ai_comment:
            lines.append("   AI 분석 이력 있음")
        if record.trigger_lines:
            lines.append(f"   대표 오류: {record.trigger_lines[0]}")
    return "\n".join(lines)
