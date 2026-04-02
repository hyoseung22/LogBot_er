from __future__ import annotations

import re
from collections import Counter, deque
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


ERROR_PATTERN = re.compile(
    r"\[ERROR\]|\bException\b|NullReferenceException|JsonReaderException|"
    r"\bFailed\b|\bCrash\b|\bUnhandled\b|\bdisconnect(?:ed|ion)?\b|"
    r"\bSocketException\b|\btimeout\b(?!:\s*0\b)|timed out|"
    r"\bD3D\b|\bGPU\b|Uploading Crash Report|LogError",
    re.IGNORECASE,
)
STACK_PATTERN = re.compile(r"^\s*(at |UnityEngine\.|Blis\.|System\.|Newtonsoft\.)")
SENSITIVE_PATTERNS = [
    (re.compile(r"[A-Za-z]:/Users/[^/\s]+", re.IGNORECASE), "C:/Users/<user>"),
    (re.compile(r"[A-Za-z]:\\Users\\[^\\\s]+", re.IGNORECASE), r"C:\Users\<user>"),
    (re.compile(r"\b\d{1,3}(?:\.\d{1,3}){3}\b"), "<ip>"),
    (re.compile(r"\b[0-9a-f]{32,64}\b", re.IGNORECASE), "<token>"),
]

PRIMARY_ENCODINGS = ["utf-8-sig", "utf-8", "cp949", "euc-kr", "utf-16"]


@dataclass
class ErrorBlock:
    index: int
    start_line: int
    end_line: int
    trigger_line: str
    text: str
    signature: str
    occurrences: int = 1


@dataclass
class LogSnapshot:
    path: str
    total_lines: int
    loaded_lines: int
    file_size: int
    used_encoding: str
    text: str
    lines: list[str]
    full_text: str
    full_lines: list[str]
    note: str | None = None


def read_log_snapshot(path: str, recent_line_count: int) -> LogSnapshot:
    file_path = Path(path)
    if not file_path.exists():
        raise FileNotFoundError(path)

    last_error: Exception | None = None
    decoded = None
    used_encoding = None
    raw_bytes = file_path.read_bytes()
    for encoding in PRIMARY_ENCODINGS:
        try:
            decoded = raw_bytes.decode(encoding)
            used_encoding = encoding
            break
        except UnicodeDecodeError as exc:
            last_error = exc

    if decoded is None or used_encoding is None:
        raise UnicodeDecodeError("unknown", b"", 0, 1, str(last_error))

    all_lines = decoded.splitlines()
    note = None
    if recent_line_count > 0 and len(all_lines) > recent_line_count:
        lines = list(deque(all_lines, maxlen=recent_line_count))
        note = f"최근 {recent_line_count}줄만 분석했습니다."
    else:
        lines = all_lines

    snapshot = LogSnapshot(
        path=str(file_path),
        total_lines=len(all_lines),
        loaded_lines=len(lines),
        file_size=file_path.stat().st_size,
        used_encoding=used_encoding,
        text="\n".join(lines),
        lines=lines,
        full_text=decoded,
        full_lines=all_lines,
        note=note,
    )

    if snapshot.file_size == 0 and file_path.name.lower() == "player.log":
        previous = file_path.with_name("Player-prev.log")
        if previous.exists() and previous.stat().st_size > 0:
            snapshot.note = (
                f"현재 Player.log는 비어 있습니다. 참고용으로 {previous.name}를 대신 선택할 수 있습니다."
            )
    return snapshot


def build_log_snapshot_from_text(
    *,
    path_label: str,
    decoded_text: str,
    used_encoding: str,
    recent_line_count: int,
) -> LogSnapshot:
    all_lines = decoded_text.splitlines()
    note = None
    if recent_line_count > 0 and len(all_lines) > recent_line_count:
        lines = list(deque(all_lines, maxlen=recent_line_count))
        note = f"최근 {recent_line_count}줄만 분석했습니다."
    else:
        lines = all_lines

    return LogSnapshot(
        path=path_label,
        total_lines=len(all_lines),
        loaded_lines=len(lines),
        file_size=len(decoded_text.encode(used_encoding, errors="replace")),
        used_encoding=used_encoding,
        text="\n".join(lines),
        lines=lines,
        full_text=decoded_text,
        full_lines=all_lines,
        note=note,
    )


def mask_sensitive_text(text: str) -> str:
    masked = text
    for pattern, replacement in SENSITIVE_PATTERNS:
        masked = pattern.sub(lambda _match, value=replacement: value, masked)
    return masked


def _normalize_signature(line: str) -> str:
    signature = re.sub(r"\d+", "<n>", line.strip())
    signature = re.sub(r"\s+", " ", signature)
    return signature[:180] or "unknown"


def _merge_ranges(ranges: Iterable[tuple[int, int, int]]) -> list[tuple[int, int, int]]:
    sorted_ranges = sorted(ranges, key=lambda item: (item[0], item[1]))
    merged: list[tuple[int, int, int]] = []
    for start, end, trigger in sorted_ranges:
        if not merged or start > merged[-1][1] + 1:
            merged.append((start, end, trigger))
            continue
        prev_start, prev_end, prev_trigger = merged[-1]
        merged[-1] = (prev_start, max(prev_end, end), min(prev_trigger, trigger))
    return merged


def extract_error_blocks(lines: list[str], before: int = 20, after: int = 30) -> list[ErrorBlock]:
    candidate_ranges: list[tuple[int, int, int]] = []
    signatures = Counter()

    for index, line in enumerate(lines):
        if ERROR_PATTERN.search(line):
            start = max(0, index - before)
            end = min(len(lines) - 1, index + after)
            stack_cursor = end + 1
            while stack_cursor < len(lines) and (
                STACK_PATTERN.search(lines[stack_cursor]) or not lines[stack_cursor].strip()
            ):
                end = stack_cursor
                stack_cursor += 1
            candidate_ranges.append((start, end, index))
            signatures[_normalize_signature(line)] += 1

    merged_ranges = _merge_ranges(candidate_ranges)
    blocks: list[ErrorBlock] = []
    for block_index, (start, end, trigger_index) in enumerate(merged_ranges, start=1):
        trigger_line = lines[trigger_index].strip()
        signature = _normalize_signature(trigger_line)
        blocks.append(
            ErrorBlock(
                index=block_index,
                start_line=start + 1,
                end_line=end + 1,
                trigger_line=trigger_line,
                text="\n".join(lines[start : end + 1]),
                signature=signature,
                occurrences=signatures[signature],
            )
        )
    return blocks
