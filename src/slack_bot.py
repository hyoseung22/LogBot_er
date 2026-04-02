from __future__ import annotations

import hashlib
import hmac
import json
import os
import sys
import threading
import time
import traceback
import urllib.parse
import urllib.request
from collections import deque
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .ai_client import request_direct_ai_comment, request_server_ai_comment
from .log_parser import (
    PRIMARY_ENCODINGS,
    build_log_snapshot_from_text,
    extract_error_blocks,
    mask_sensitive_text,
)


_PROCESSED_EVENTS_LOCK = threading.Lock()
_PROCESSED_EVENTS: dict[str, float] = {}
_PROCESSED_EVENTS_ORDER: deque[tuple[str, float]] = deque()


class SlackApiError(RuntimeError):
    def __init__(self, method_name: str, error_code: str) -> None:
        self.method_name = method_name
        self.error_code = error_code
        super().__init__(f"Slack API {method_name} failed: {error_code}")


def _log_path() -> Path:
    if getattr(sys, "frozen", False):
        base_dir = Path(sys.executable).resolve().parent
    else:
        base_dir = Path(__file__).resolve().parents[1]
    return base_dir / "data" / "slack-bot.log"


def _log(message: str) -> None:
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{timestamp}] {message}"
    print(line, flush=True)
    try:
        log_file = _log_path()
        log_file.parent.mkdir(parents=True, exist_ok=True)
        with log_file.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")
    except OSError:
        pass


def _allowed_channels() -> set[str]:
    raw = os.getenv("SLACK_ALLOWED_CHANNELS", "")
    return {item.strip() for item in raw.split(",") if item.strip()}


def _analysis_mode() -> str:
    mode = os.getenv("SLACK_ANALYSIS_MODE", "server").strip().lower()
    if mode not in {"local", "direct", "server"}:
        return "server"
    return mode


def _decode_log_bytes(raw: bytes) -> tuple[str, str]:
    last_error: Exception | None = None
    for encoding in PRIMARY_ENCODINGS:
        try:
            return raw.decode(encoding), encoding
        except UnicodeDecodeError as exc:
            last_error = exc
    raise UnicodeDecodeError("unknown", b"", 0, 1, str(last_error))


def _slack_bot_token() -> str:
    return os.getenv("SLACK_BOT_TOKEN", "").strip()


def _message_chunk_size() -> int:
    try:
        size = int(os.getenv("SLACK_MESSAGE_CHUNK_SIZE", "3500"))
    except ValueError:
        return 3500
    return max(500, size)


def _processed_event_ttl_seconds() -> int:
    try:
        value = int(os.getenv("SLACK_EVENT_DEDUP_TTL_SECONDS", "3600"))
    except ValueError:
        return 3600
    return max(60, value)


def _skip_signature_verification() -> bool:
    return os.getenv("SLACK_SKIP_SIGNATURE_VERIFICATION", "").strip() == "1"


def _slack_api_request(
    method_name: str,
    *,
    payload: dict[str, object] | None = None,
    params: dict[str, str] | None = None,
) -> dict[str, object]:
    token = _slack_bot_token()
    if not token:
        raise RuntimeError("SLACK_BOT_TOKEN is missing.")

    url = f"https://slack.com/api/{method_name}"
    if params:
        url += "?" + urllib.parse.urlencode(params)

    body = None
    headers = {"Authorization": f"Bearer {token}"}
    if payload is not None:
        body = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json; charset=utf-8"

    request = urllib.request.Request(url, data=body, headers=headers, method="POST" if body else "GET")
    with urllib.request.urlopen(request, timeout=20) as response:
        response_body = json.loads(response.read().decode("utf-8"))

    if not isinstance(response_body, dict):
        raise RuntimeError(f"Slack API {method_name} returned an invalid response.")
    if response_body.get("ok") is not True:
        raise SlackApiError(method_name, str(response_body.get("error", "unknown_error")))
    return response_body


def _split_message(text: str, limit: int) -> list[str]:
    if len(text) <= limit:
        return [text]

    chunks: list[str] = []
    current: list[str] = []
    current_length = 0
    for line in text.splitlines():
        line_length = len(line) + 1
        if current and current_length + line_length > limit:
            chunks.append("\n".join(current))
            current = [line]
            current_length = line_length
            continue
        if not current and line_length > limit:
            for start in range(0, len(line), limit):
                chunks.append(line[start : start + limit])
            current = []
            current_length = 0
            continue
        current.append(line)
        current_length += line_length

    if current:
        chunks.append("\n".join(current))
    return [chunk for chunk in chunks if chunk]


def _post_slack_message(channel: str, thread_ts: str, text: str) -> None:
    for index, chunk in enumerate(_split_message(text, _message_chunk_size()), start=1):
        payload = {"channel": channel, "thread_ts": thread_ts, "text": chunk}
        _slack_api_request("chat.postMessage", payload=payload)
        if index > 1:
            time.sleep(0.2)


def _download_slack_file(download_url: str) -> bytes:
    token = _slack_bot_token()
    if not token:
        raise RuntimeError("SLACK_BOT_TOKEN is missing.")
    request = urllib.request.Request(
        download_url,
        headers={"Authorization": f"Bearer {token}"},
        method="GET",
    )
    with urllib.request.urlopen(request, timeout=40) as response:
        return response.read()


def _fetch_slack_file_info(file_id: str) -> dict[str, object] | None:
    if not file_id:
        return None
    response_body = _slack_api_request("files.info", params={"file": file_id})
    file_info = response_body.get("file")
    return file_info if isinstance(file_info, dict) else None


def _fetch_conversation_message(channel: str, ts: str) -> dict[str, object] | None:
    if not channel or not ts:
        return None
    response_body = _slack_api_request(
        "conversations.history",
        params={
            "channel": channel,
            "oldest": ts,
            "latest": ts,
            "inclusive": "true",
            "limit": "1",
        },
    )
    messages = response_body.get("messages", [])
    if not isinstance(messages, list):
        return None
    for message in messages:
        if isinstance(message, dict):
            return message
    return None


def _fetch_recent_conversation_messages(channel: str, latest: str, limit: int = 10) -> list[dict[str, object]]:
    if not channel:
        return []
    params = {"channel": channel, "limit": str(max(1, limit))}
    if latest:
        params["latest"] = latest
    response_body = _slack_api_request("conversations.history", params=params)
    messages = response_body.get("messages", [])
    if not isinstance(messages, list):
        return []
    return [message for message in messages if isinstance(message, dict)]


def _resolve_file_info(file_info: dict[str, object]) -> dict[str, object]:
    if file_info.get("url_private_download") or file_info.get("url_private"):
        return file_info

    file_id = str(file_info.get("id", "")).strip()
    fetched_file = _fetch_slack_file_info(file_id)
    if fetched_file:
        return fetched_file
    return file_info


def _is_supported_file_name(file_name: str, mimetype: str = "") -> bool:
    lowered = file_name.lower()
    if lowered.endswith(".log") or lowered.endswith(".txt"):
        return True
    return mimetype.startswith("text/")


def _build_event_cache_key(event_id: str, event: dict[str, object]) -> str:
    if event_id:
        return f"event:{event_id}"

    event_type = str(event.get("type", "")).strip()
    channel = str(event.get("channel", "")).strip()
    ts = str(event.get("ts", "")).strip()
    file_ids: list[str] = []
    for item in event.get("files", []):
        if isinstance(item, dict):
            file_id = str(item.get("id", "")).strip()
            if file_id:
                file_ids.append(file_id)
    file_ids.sort()
    return f"fallback:{event_type}:{channel}:{ts}:{'|'.join(file_ids)}"


def _mark_event_processed(cache_key: str) -> bool:
    now = time.time()
    ttl = _processed_event_ttl_seconds()
    with _PROCESSED_EVENTS_LOCK:
        while _PROCESSED_EVENTS_ORDER:
            oldest_key, created_at = _PROCESSED_EVENTS_ORDER[0]
            if now - created_at <= ttl:
                break
            _PROCESSED_EVENTS_ORDER.popleft()
            if _PROCESSED_EVENTS.get(oldest_key) == created_at:
                _PROCESSED_EVENTS.pop(oldest_key, None)

        if cache_key in _PROCESSED_EVENTS:
            return False
        _PROCESSED_EVENTS[cache_key] = now
        _PROCESSED_EVENTS_ORDER.append((cache_key, now))
        return True


def _build_mention_help_message() -> str:
    return (
        "분석할 `.log` 또는 `.txt` 파일을 찾지 못했습니다.\n"
        "다음 둘 중 하나로 호출해 주세요.\n"
        "- 파일 업로드 메시지에 봇 멘션을 함께 넣기\n"
        "- 파일을 올린 뒤 그 메시지 스레드에서 봇을 멘션하기"
    )


def _history_scope_for_channel(channel: str, channel_type: str = "") -> str:
    normalized_type = channel_type.strip().lower()
    if normalized_type == "channel" or channel.startswith("C"):
        return "`channels:history`"
    if normalized_type == "group" or channel.startswith("G"):
        return "`groups:history`"
    if normalized_type == "im" or channel.startswith("D"):
        return "`im:history`"
    if normalized_type == "mpim":
        return "`mpim:history`"
    return "`channels:history`"


def _build_user_visible_error_message(exc: Exception, event: dict[str, object]) -> str:
    channel = str(event.get("channel", ""))
    channel_type = str(event.get("channel_type", ""))
    if isinstance(exc, SlackApiError):
        if exc.error_code == "missing_scope" and exc.method_name == "conversations.history":
            required_scope = _history_scope_for_channel(channel, channel_type)
            return (
                "업로드된 파일 메시지를 찾기 위한 Slack 읽기 권한이 부족합니다. "
                f"Slack 앱에 {required_scope} scope를 추가하고 `Reinstall to Workspace`를 다시 실행해 주세요."
            )
        if exc.error_code == "missing_scope" and exc.method_name == "files.info":
            return (
                "파일 정보를 읽을 권한이 부족합니다. "
                "Slack 앱에 `files:read` scope를 추가하고 `Reinstall to Workspace`를 다시 실행해 주세요."
            )
        if exc.error_code == "invalid_auth":
            return (
                "Slack 봇 토큰이 유효하지 않습니다. "
                "`OAuth & Permissions`의 `Bot User OAuth Token`을 `.env`의 `SLACK_BOT_TOKEN`에 다시 넣고 "
                "봇을 재시작해 주세요."
            )

    if isinstance(exc, RuntimeError) and str(exc) == "OPENAI_API_KEY is missing.":
        return "Render 환경변수에 `OPENAI_API_KEY`가 없습니다. Render Dashboard의 Environment에서 키를 넣고 재배포해 주세요."
    if isinstance(exc, RuntimeError) and str(exc) == "AI analysis request failed.":
        return "AI 분석 호출에 실패했습니다. 분석 서버 연결과 `OPENAI_API_KEY` 설정을 확인해 주세요."
    if isinstance(exc, RuntimeError) and str(exc) == "AI-only mode requires direct or server analysis mode.":
        return "현재 Slack bot은 AI 분석 전용입니다. `SLACK_ANALYSIS_MODE`를 `server` 또는 `direct`로 설정해 주세요."
    return "멘션 기반 분석 중 오류가 발생했습니다. 서버 설정과 권한을 확인해 주세요."


def _build_no_error_message() -> str:
    return (
        "[로그 분석 결과]\n"
        "- 눈에 띄는 오류 블록을 찾지 못했습니다.\n"
        "한줄 요약: 분석 가능한 대표 오류가 없습니다."
    )


def _build_result_message(result_text: str) -> str:
    return result_text.strip()


def _run_analysis_for_text(file_name: str, decoded_text: str, used_encoding: str) -> str:
    recent_line_count = int(os.getenv("SLACK_RECENT_LINE_COUNT", "3000"))
    snapshot = build_log_snapshot_from_text(
        path_label=file_name,
        decoded_text=decoded_text,
        used_encoding=used_encoding,
        recent_line_count=recent_line_count,
    )
    if os.getenv("SLACK_MASK_SENSITIVE", "1") != "0":
        masked_full = mask_sensitive_text(snapshot.full_text)
        snapshot = build_log_snapshot_from_text(
            path_label=snapshot.path,
            decoded_text=masked_full,
            used_encoding=snapshot.used_encoding,
            recent_line_count=recent_line_count,
        )

    blocks = extract_error_blocks(snapshot.lines)
    if len(blocks) <= 1 and snapshot.total_lines > snapshot.loaded_lines:
        blocks = extract_error_blocks(snapshot.full_lines)

    if not blocks:
        return _build_no_error_message()

    mode = _analysis_mode()
    analysis: str | None = None
    if mode == "direct":
        if not os.getenv("OPENAI_API_KEY"):
            raise RuntimeError("OPENAI_API_KEY is missing.")
        analysis = request_direct_ai_comment(snapshot, blocks, None)
    elif mode == "server":
        analysis = request_server_ai_comment(
            os.getenv("SLACK_ANALYSIS_SERVER_URL", "http://127.0.0.1:8765"),
            snapshot,
            blocks,
            None,
        )
    else:
        raise RuntimeError("AI-only mode requires direct or server analysis mode.")

    if not analysis:
        _log(
            "AI analysis request failed "
            f"mode={mode} "
            f"api_key_present={bool(os.getenv('OPENAI_API_KEY'))} "
            f"model={os.getenv('OPENAI_MODEL') or 'gpt-5-mini'}"
        )
        raise RuntimeError("AI analysis request failed.")
    return analysis.strip()


def _find_supported_file_in_messages(
    messages: list[dict[str, object]],
) -> tuple[dict[str, object] | None, dict[str, object] | None]:
    for message in messages:
        files = message.get("files", [])
        if not isinstance(files, list):
            continue
        for raw_file_info in files:
            if not isinstance(raw_file_info, dict):
                continue
            file_info = _resolve_file_info(raw_file_info)
            file_name = str(file_info.get("name", ""))
            download_url = str(file_info.get("url_private_download") or file_info.get("url_private") or "")
            mimetype = str(file_info.get("mimetype", ""))
            if not file_name or not download_url:
                continue
            if _is_supported_file_name(file_name, mimetype):
                return file_info, message
    return None, None


def _find_file_for_mention(event: dict[str, object]) -> tuple[dict[str, object] | None, dict[str, object] | None]:
    channel = str(event.get("channel", ""))
    ts = str(event.get("ts", ""))
    thread_ts = str(event.get("thread_ts", ""))
    user_id = str(event.get("user", ""))

    candidates: list[dict[str, object]] = []
    history_error: SlackApiError | None = None

    candidates.append(event)

    file_info, source_message = _find_supported_file_in_messages(candidates)
    if file_info:
        return file_info, source_message

    try:
        current_message = _fetch_conversation_message(channel, ts)
    except SlackApiError as exc:
        history_error = exc
        current_message = None
    if current_message and current_message not in candidates:
        candidates.append(current_message)

    if thread_ts and thread_ts != ts:
        try:
            root_message = _fetch_conversation_message(channel, thread_ts)
        except SlackApiError as exc:
            history_error = exc
            root_message = None
        if root_message and root_message not in candidates:
            candidates.append(root_message)

    file_info, source_message = _find_supported_file_in_messages(candidates)
    if file_info:
        return file_info, source_message

    try:
        recent_messages = _fetch_recent_conversation_messages(channel, ts, limit=10)
    except SlackApiError as exc:
        if history_error is None:
            history_error = exc
        recent_messages = []

    prioritized_recent: list[dict[str, object]] = []
    remaining_recent: list[dict[str, object]] = []
    for message in recent_messages:
        if message in candidates:
            continue
        if str(message.get("user", "")) == user_id:
            prioritized_recent.append(message)
        else:
            remaining_recent.append(message)

    file_info, source_message = _find_supported_file_in_messages(prioritized_recent + remaining_recent)
    if file_info:
        return file_info, source_message

    if history_error is not None:
        raise history_error
    return None, None


def _process_mention_event(event: dict[str, object]) -> None:
    channel = str(event.get("channel", ""))
    thread_ts = str(event.get("thread_ts") or event.get("ts") or "")
    if not channel or not thread_ts:
        return

    allowed_channels = _allowed_channels()
    if allowed_channels and channel not in allowed_channels:
        return

    try:
        file_info, _source_message = _find_file_for_mention(event)
        if not file_info:
            _log(f"No supported file found for mention in channel={channel} thread={thread_ts}")
            _post_slack_message(channel, thread_ts, _build_mention_help_message())
            return

        file_name = str(file_info.get("name", ""))
        download_url = str(file_info.get("url_private_download") or file_info.get("url_private") or "")
        if not file_name or not download_url:
            _log(f"Supported file metadata incomplete in channel={channel} thread={thread_ts}")
            _post_slack_message(channel, thread_ts, _build_mention_help_message())
            return

        _log(f"Starting analysis for Slack file '{file_name}' in channel={channel} thread={thread_ts}")
        _post_slack_message(channel, thread_ts, f"`{file_name}` 분석을 시작합니다.")
        raw = _download_slack_file(download_url)
        decoded_text, used_encoding = _decode_log_bytes(raw)
        result_text = _run_analysis_for_text(file_name, decoded_text, used_encoding)
        _post_slack_message(channel, thread_ts, _build_result_message(result_text))
        _log(f"Finished analysis for Slack file '{file_name}'")
    except Exception as exc:
        _log(f"Mention-triggered analysis failed: {exc}\n{traceback.format_exc()}")
        _post_slack_message(channel, thread_ts, _build_user_visible_error_message(exc, event))


class SlackEventHandler(BaseHTTPRequestHandler):
    server_version = "PlayerLogSlackBot/0.1"

    def do_GET(self) -> None:
        if self.path != "/health":
            self._write_json({"error": "not found"}, HTTPStatus.NOT_FOUND)
            return
        self._write_json(
            {
                "status": "ok",
                "mode": _analysis_mode(),
                "ai_key_present": bool(os.getenv("OPENAI_API_KEY")),
                "openai_model": os.getenv("OPENAI_MODEL") or "gpt-5-mini",
                "allowed_channels": sorted(_allowed_channels()),
            }
        )

    def do_POST(self) -> None:
        if self.path != "/slack/events":
            self._write_json({"error": "not found"}, HTTPStatus.NOT_FOUND)
            return

        body = self._read_body()
        if body is None:
            self._write_json({"error": "invalid request"}, HTTPStatus.BAD_REQUEST)
            return

        try:
            payload = json.loads(body.decode("utf-8"))
        except json.JSONDecodeError:
            self._write_json({"error": "invalid json"}, HTTPStatus.BAD_REQUEST)
            return

        _log(f"Incoming Slack POST type={payload.get('type', '?')}")

        if payload.get("type") == "url_verification":
            _log("Responding to Slack url_verification challenge")
            self._write_json({"challenge": payload.get("challenge", "")})
            return

        if not self._verify_slack_signature(body):
            _log("Rejected Slack request due to invalid signature")
            self._write_json({"error": "invalid signature"}, HTTPStatus.UNAUTHORIZED)
            return

        if payload.get("type") == "event_callback":
            event_id = str(payload.get("event_id", "")).strip()
            event = payload.get("event", {})
            if isinstance(event, dict):
                _log(
                    "Slack event callback "
                    f"type={event.get('type', '?')} "
                    f"subtype={event.get('subtype', '')} "
                    f"channel={event.get('channel', '')}"
                )
                if event.get("type") == "app_mention":
                    cache_key = _build_event_cache_key(event_id, event)
                    if _mark_event_processed(cache_key):
                        _log(f"Accepted Slack event {cache_key}")
                        threading.Thread(target=_process_mention_event, args=(event,), daemon=True).start()
                    else:
                        _log(f"Ignored duplicate Slack event {cache_key}")
            self._write_json({"ok": True})
            return

        self._write_json({"ok": True})

    def log_message(self, format: str, *args: object) -> None:
        return

    def _read_body(self) -> bytes | None:
        length_header = self.headers.get("Content-Length")
        if not length_header:
            return None
        try:
            content_length = int(length_header)
        except ValueError:
            return None
        return self.rfile.read(content_length)

    def _verify_slack_signature(self, body: bytes) -> bool:
        if _skip_signature_verification():
            return True

        signing_secret = os.getenv("SLACK_SIGNING_SECRET", "").encode("utf-8")
        if not signing_secret:
            return True

        timestamp = self.headers.get("X-Slack-Request-Timestamp", "")
        signature = self.headers.get("X-Slack-Signature", "")
        if not timestamp or not signature:
            return False
        try:
            ts_value = int(timestamp)
        except ValueError:
            return False
        if abs(time.time() - ts_value) > 60 * 5:
            return False

        basestring = f"v0:{timestamp}:".encode("utf-8") + body
        digest = "v0=" + hmac.new(signing_secret, basestring, hashlib.sha256).hexdigest()
        return hmac.compare_digest(digest, signature)

    def _write_json(self, payload: dict[str, object], status: HTTPStatus = HTTPStatus.OK) -> None:
        encoded = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)


def main() -> None:
    host = os.getenv("SLACK_BOT_HOST", "0.0.0.0")
    port = int(os.getenv("PORT") or os.getenv("SLACK_BOT_PORT", "8780"))
    server = ThreadingHTTPServer((host, port), SlackEventHandler)
    print(f"Slack bot listening on http://{host}:{port}")
    server.serve_forever()
