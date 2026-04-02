from __future__ import annotations

import json
import os
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .ai_client import _extract_texts


def _build_server_prompt(payload: dict[str, object]) -> str:
    blocks = payload.get("error_blocks", [])
    if not isinstance(blocks, list):
        blocks = []

    excerpt_chunks = []
    for block in blocks[:5]:
        if not isinstance(block, dict):
            continue
        excerpt_chunks.append(
            "- 구간: {start}-{end}줄\n"
            "  횟수: {count}회\n"
            "  대표 내용: {body}".format(
                start=block.get("start_line", "?"),
                end=block.get("end_line", "?"),
                count=block.get("occurrences", "?"),
                body=str(block.get("trigger_line", "") or block.get("text", "")).replace("|", "/"),
            )
        )

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
        f"[클라이언트]\n이름={payload.get('client_name', '')}\n"
        f"로그={payload.get('log_path', '')}\n"
        f"전체줄={payload.get('total_lines', '')}\n"
        f"분석줄={payload.get('analyzed_lines', '')}\n"
        f"인코딩={payload.get('encoding', '')}\n\n"
        f"[오류 블록]\n{chr(10).join(excerpt_chunks) if excerpt_chunks else '없음'}"
    )


def _request_openai(prompt: str) -> str | None:
    import urllib.error
    import urllib.request

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


class AnalysisHandler(BaseHTTPRequestHandler):
    server_version = "PlayerLogAIServer/0.1"

    def do_GET(self) -> None:
        if self.path != "/health":
            self._write_json({"error": "not found"}, HTTPStatus.NOT_FOUND)
            return
        self._write_json(
            {
                "status": "ok",
                "ai_available": bool(os.getenv("OPENAI_API_KEY")),
                "model": os.getenv("OPENAI_MODEL") or "gpt-5-mini",
            }
        )

    def do_POST(self) -> None:
        if self.path != "/analyze-log":
            self._write_json({"error": "not found"}, HTTPStatus.NOT_FOUND)
            return

        length_header = self.headers.get("Content-Length")
        if not length_header:
            self._write_json({"error": "missing content length"}, HTTPStatus.BAD_REQUEST)
            return

        try:
            content_length = int(length_header)
            raw = self.rfile.read(content_length)
            payload = json.loads(raw.decode("utf-8"))
        except (ValueError, json.JSONDecodeError):
            self._write_json({"error": "invalid json"}, HTTPStatus.BAD_REQUEST)
            return

        if not isinstance(payload, dict):
            self._write_json({"error": "payload must be an object"}, HTTPStatus.BAD_REQUEST)
            return

        prompt = _build_server_prompt(payload)
        analysis = _request_openai(prompt)
        if not analysis:
            self._write_json(
                {"error": "ai request failed or OPENAI_API_KEY is missing"},
                HTTPStatus.BAD_GATEWAY,
            )
            return

        self._write_json({"analysis": analysis})

    def log_message(self, format: str, *args: object) -> None:
        return

    def _write_json(self, payload: dict[str, object], status: HTTPStatus = HTTPStatus.OK) -> None:
        encoded = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)


def main() -> None:
    host = os.getenv("PLAYER_LOG_AI_SERVER_HOST", "0.0.0.0")
    port = int(os.getenv("PORT") or os.getenv("PLAYER_LOG_AI_SERVER_PORT", "8765"))
    server = ThreadingHTTPServer((host, port), AnalysisHandler)
    print(f"Player Log AI server listening on http://{host}:{port}")
    server.serve_forever()
