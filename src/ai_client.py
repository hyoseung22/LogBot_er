from __future__ import annotations

import json
import os
import socket
import urllib.error
import urllib.request

from .heuristics import AnalysisResult
from .log_parser import ErrorBlock, LogSnapshot


def _build_block_excerpt(block: ErrorBlock) -> str:
    preview = block.trigger_line.strip() or block.text.strip().splitlines()[0]
    preview = preview.replace("|", "/")
    return (
        f"- 구간: {block.start_line}-{block.end_line}줄\n"
        f"  횟수: {block.occurrences}회\n"
        f"  대표 내용: {preview}\n"
    )


def build_prompt(
    snapshot: LogSnapshot,
    blocks: list[ErrorBlock],
    local_result: AnalysisResult | None = None,
) -> str:
    excerpt = "\n".join(_build_block_excerpt(block) for block in blocks[:5]).strip()
    no_error_text = (
        "[로그 분석 결과]\n"
        "- 눈에 띄는 오류 블록을 찾지 못했습니다."
    )
    return (
        "당신은 Slack에 짧게 올리는 에러 로그 분석 봇이다.\n"
        "반드시 한국어로만 답하고, 장황한 설명을 금지한다.\n"
        "오류 발생 구간, 대표 내용, 발생 횟수만 간단히 요약한다.\n"
        "대표 내용은 짧게 줄여 쓰고 `|` 문자를 쓰지 마라.\n"
        "원인 추정, 권장 조치, 추가 설명, 배경 설명, 마지막 요약 문장은 쓰지 마라.\n"
        "출력은 최대 4개 bullet까지만 쓴다.\n"
        "출력 형식은 아래를 정확히 따른다.\n"
        "[로그 분석 결과]\n"
        "- {start}-{end}줄 | {대표 내용} | {횟수}회\n"
        "- {start}-{end}줄 | {대표 내용} | {횟수}회\n"
        "오류가 없으면 아래 형식으로만 답한다.\n"
        f"{no_error_text}\n\n"
        f"[파일]\n이름={snapshot.path}\n전체줄={snapshot.total_lines}\n분석줄={snapshot.loaded_lines}\n"
        f"인코딩={snapshot.used_encoding}\n\n"
        f"[오류 블록]\n{excerpt or '없음'}"
    )


def build_server_payload(
    snapshot: LogSnapshot,
    blocks: list[ErrorBlock],
    local_result: AnalysisResult | None = None,
) -> dict[str, object]:
    return {
        "client_name": socket.gethostname(),
        "log_path": snapshot.path,
        "total_lines": snapshot.total_lines,
        "analyzed_lines": snapshot.loaded_lines,
        "file_size": snapshot.file_size,
        "encoding": snapshot.used_encoding,
        "error_blocks": [
            {
                "index": block.index,
                "start_line": block.start_line,
                "end_line": block.end_line,
                "trigger_line": block.trigger_line,
                "occurrences": block.occurrences,
                "text": block.text,
            }
            for block in blocks[:5]
        ],
    }


def _extract_texts(body: dict[str, object]) -> str | None:
    output = body.get("output", [])
    if not isinstance(output, list):
        return None

    texts: list[str] = []
    for item in output:
        if not isinstance(item, dict):
            continue
        for content in item.get("content", []):
            if isinstance(content, dict):
                text = content.get("text")
                if isinstance(text, str):
                    texts.append(text.strip())
    combined = "\n\n".join(text for text in texts if text)
    return combined or None


def _request_openai_responses(prompt: str) -> str | None:
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        return None

    model = os.getenv("OPENAI_MODEL") or "gpt-5-mini"
    payload = {
        "model": model,
        "max_output_tokens": 400,
        "reasoning": {"effort": "low"},
        "text": {"verbosity": "low"},
        "input": [
            {
                "role": "user",
                "content": [{"type": "input_text", "text": prompt}],
            }
        ],
    }
    request = urllib.request.Request(
        "https://api.openai.com/v1/responses",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=40) as response:
            body = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, json.JSONDecodeError):
        return None

    return _extract_texts(body)


def request_direct_ai_comment(
    snapshot: LogSnapshot,
    blocks: list[ErrorBlock],
    local_result: AnalysisResult | None = None,
) -> str | None:
    prompt = build_prompt(snapshot, blocks, local_result)
    return _request_openai_responses(prompt)


def request_server_ai_comment(
    server_url: str,
    snapshot: LogSnapshot,
    blocks: list[ErrorBlock],
    local_result: AnalysisResult | None = None,
) -> str | None:
    payload = build_server_payload(snapshot, blocks, local_result)
    base_url = server_url.rstrip("/")
    request = urllib.request.Request(
        f"{base_url}/analyze-log",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=40) as response:
            body = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, json.JSONDecodeError):
        return None

    comment = body.get("analysis")
    if isinstance(comment, str) and comment.strip():
        return comment.strip()
    return None
