from __future__ import annotations

import os
import queue
import threading
import traceback
from dataclasses import replace
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from .ai_client import request_direct_ai_comment
from .config_store import build_default_log_path, load_config, save_config
from .heuristics import AnalysisResult, build_local_analysis
from .history_store import append_history, build_record, find_similar_history, format_history_summary
from .log_parser import extract_error_blocks, mask_sensitive_text, read_log_snapshot


ANALYSIS_MODE_LABELS = {
    "local": "로컬 분석만",
    "direct": "이 PC에서 직접 AI 분석",
}


class AnalyzerApp:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("Player.log AI 분석기")
        self.root.geometry("1160x900")
        self.root.minsize(980, 760)

        self.config = load_config()
        self.result_queue: queue.Queue[tuple[str, object]] = queue.Queue()
        self.is_analyzing = False
        self.last_result: AnalysisResult | None = None
        self.last_log_preview = ""
        self.last_history_summary = ""

        self._build_styles()
        self._build_layout()
        self._load_initial_state()
        self._poll_queue()

    def _build_styles(self) -> None:
        self.root.configure(bg="#f2efe8")
        style = ttk.Style(self.root)
        style.theme_use("clam")
        style.configure("App.TFrame", background="#f2efe8")
        style.configure("Card.TLabelframe", background="#fbf8f1", bordercolor="#d3c7b5")
        style.configure("Card.TLabelframe.Label", background="#fbf8f1", foreground="#2c2218")
        style.configure("App.TLabel", background="#f2efe8", foreground="#2c2218")
        style.configure("Status.TLabel", background="#f2efe8", foreground="#5b4f43")
        style.configure("Accent.TButton", background="#2f6c5a", foreground="#ffffff")

    def _build_layout(self) -> None:
        outer = ttk.Frame(self.root, style="App.TFrame", padding=16)
        outer.pack(fill="both", expand=True)
        outer.columnconfigure(0, weight=1)
        outer.rowconfigure(4, weight=1)
        outer.rowconfigure(5, weight=1)

        header = ttk.Frame(outer, style="App.TFrame")
        header.grid(row=0, column=0, sticky="ew", pady=(0, 12))
        ttk.Label(
            header,
            text="Player.log AI 분석기",
            font=("Malgun Gothic", 20, "bold"),
            style="App.TLabel",
        ).pack(anchor="w")
        ttk.Label(
            header,
            text="개인용 로컬 도구입니다. 필요할 때만 이 PC에서 직접 AI 분석을 보강하고, 분석 이력은 누적 저장합니다.",
            font=("Malgun Gothic", 10),
            style="App.TLabel",
        ).pack(anchor="w", pady=(4, 0))

        path_frame = ttk.LabelFrame(outer, text="로그 파일 경로", style="Card.TLabelframe", padding=12)
        path_frame.grid(row=1, column=0, sticky="ew", pady=(0, 12))
        path_frame.columnconfigure(0, weight=1)

        self.path_var = tk.StringVar()
        self.path_entry = ttk.Entry(path_frame, textvariable=self.path_var, font=("Consolas", 10))
        self.path_entry.grid(row=0, column=0, sticky="ew", padx=(0, 8))

        self.browse_button = ttk.Button(path_frame, text="찾아보기", command=self._choose_file)
        self.browse_button.grid(row=0, column=1, padx=(0, 6))

        self.restore_button = ttk.Button(path_frame, text="기본값 복원", command=self._restore_default_path)
        self.restore_button.grid(row=0, column=2)

        self.path_status_var = tk.StringVar(value="경로를 확인해 주세요.")
        ttk.Label(
            path_frame,
            textvariable=self.path_status_var,
            style="Status.TLabel",
            font=("Malgun Gothic", 9),
        ).grid(row=1, column=0, columnspan=3, sticky="w", pady=(8, 0))

        option_frame = ttk.LabelFrame(outer, text="분석 옵션", style="Card.TLabelframe", padding=12)
        option_frame.grid(row=2, column=0, sticky="ew", pady=(0, 12))
        option_frame.columnconfigure(1, weight=1)

        self.analysis_mode_var = tk.StringVar(value=self.config.analysis_mode)
        self.mask_var = tk.BooleanVar(value=self.config.mask_sensitive_data)
        self.recent_line_var = tk.StringVar(value=str(self.config.recent_line_count))

        ttk.Radiobutton(
            option_frame,
            text="로컬 분석만",
            value="local",
            variable=self.analysis_mode_var,
            command=self._save_runtime_settings,
        ).grid(row=0, column=0, sticky="w")
        ttk.Radiobutton(
            option_frame,
            text="이 PC에서 직접 AI 분석",
            value="direct",
            variable=self.analysis_mode_var,
            command=self._save_runtime_settings,
        ).grid(row=1, column=0, sticky="w", pady=(6, 0))

        self.mode_notice_var = tk.StringVar()
        ttk.Label(
            option_frame,
            textvariable=self.mode_notice_var,
            style="Status.TLabel",
            wraplength=760,
            justify="left",
        ).grid(row=0, column=1, rowspan=2, sticky="nw", padx=(12, 0))

        ttk.Checkbutton(
            option_frame,
            text="민감정보 마스킹",
            variable=self.mask_var,
            command=self._save_runtime_settings,
        ).grid(row=2, column=0, sticky="w", pady=(12, 0))

        ttk.Label(option_frame, text="최근 분석 줄 수", style="Status.TLabel").grid(
            row=3, column=0, sticky="w", pady=(10, 0)
        )
        self.recent_line_spinbox = tk.Spinbox(
            option_frame,
            from_=500,
            to=20000,
            increment=500,
            textvariable=self.recent_line_var,
            width=8,
            command=self._save_runtime_settings,
        )
        self.recent_line_spinbox.grid(row=3, column=0, sticky="e", pady=(10, 0))
        self.recent_line_spinbox.bind("<FocusOut>", lambda _event: self._save_runtime_settings())

        action_frame = ttk.LabelFrame(outer, text="실행", style="Card.TLabelframe", padding=12)
        action_frame.grid(row=3, column=0, sticky="ew", pady=(0, 12))
        action_frame.columnconfigure(3, weight=1)

        self.analyze_button = ttk.Button(
            action_frame,
            text="분석",
            command=self._start_analysis,
            style="Accent.TButton",
        )
        self.analyze_button.grid(row=0, column=0, padx=(0, 8))

        self.copy_button = ttk.Button(action_frame, text="결과 복사", command=self._copy_result, state="disabled")
        self.copy_button.grid(row=0, column=1, padx=(0, 8))

        self.copy_log_button = ttk.Button(action_frame, text="근거 로그 복사", command=self._copy_log, state="disabled")
        self.copy_log_button.grid(row=0, column=2, padx=(0, 12))

        self.status_var = tk.StringVar(value="분석 준비 완료")
        ttk.Label(
            action_frame,
            textvariable=self.status_var,
            style="Status.TLabel",
            font=("Malgun Gothic", 10),
        ).grid(row=0, column=3, sticky="w")

        self.progress = ttk.Progressbar(action_frame, mode="indeterminate", length=180)
        self.progress.grid(row=0, column=4, sticky="e")

        results_frame = ttk.Frame(outer, style="App.TFrame")
        results_frame.grid(row=4, column=0, sticky="nsew", pady=(0, 12))
        for index in range(2):
            results_frame.columnconfigure(index, weight=1)
        for index in range(4):
            results_frame.rowconfigure(index, weight=1)

        self.summary_text = self._make_card(results_frame, "문제 요약", 0, 0)
        self.cause_text = self._make_card(results_frame, "가능성 높은 원인", 0, 1)
        self.action_text = self._make_card(results_frame, "권장 조치", 1, 0)
        self.followup_text = self._make_card(results_frame, "추가 확인 항목", 1, 1)
        self.meta_text = self._make_card(results_frame, "분석 메타정보", 2, 0)
        self.evidence_text = self._make_card(results_frame, "근거 로그 요약", 2, 1)
        self.history_text = self._make_card(results_frame, "누적 히스토리 비교", 3, 0, columnspan=2)

        preview_frame = ttk.LabelFrame(outer, text="근거 로그", style="Card.TLabelframe", padding=12)
        preview_frame.grid(row=5, column=0, sticky="nsew")
        preview_frame.columnconfigure(0, weight=1)
        preview_frame.rowconfigure(0, weight=1)

        self.preview_text = tk.Text(
            preview_frame,
            wrap="none",
            height=14,
            font=("Consolas", 10),
            bg="#fffdfa",
            fg="#261d14",
        )
        self.preview_text.grid(row=0, column=0, sticky="nsew")
        self.preview_text.configure(state="disabled")

        preview_scroll_y = ttk.Scrollbar(preview_frame, orient="vertical", command=self.preview_text.yview)
        preview_scroll_y.grid(row=0, column=1, sticky="ns")
        self.preview_text.configure(yscrollcommand=preview_scroll_y.set)

    def _make_card(
        self,
        parent: ttk.Frame,
        title: str,
        row: int,
        column: int,
        columnspan: int = 1,
    ) -> tk.Text:
        frame = ttk.LabelFrame(parent, text=title, style="Card.TLabelframe", padding=10)
        frame.grid(
            row=row,
            column=column,
            columnspan=columnspan,
            sticky="nsew",
            padx=(0 if column == 0 else 6, 6 if columnspan == 1 and column == 0 else 0),
            pady=6,
        )
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(0, weight=1)

        text = tk.Text(
            frame,
            wrap="word",
            height=8,
            font=("Malgun Gothic", 10),
            bg="#fffdfa",
            fg="#261d14",
        )
        text.grid(row=0, column=0, sticky="nsew")
        text.configure(state="disabled")
        return text

    def _load_initial_state(self) -> None:
        self.path_var.set(self.config.log_path)
        self.path_var.trace_add("write", self._on_path_changed)
        self._refresh_path_status()
        self._update_mode_notice()
        self._set_text(self.summary_text, "Player.log 파일을 선택한 뒤 분석 버튼을 눌러 주세요.")
        self._set_text(self.history_text, "이전 유사 로그 기록이 없습니다. 이번 분석부터 누적 저장됩니다.")

    def _on_path_changed(self, *_args: object) -> None:
        self.config = replace(self.config, log_path=self.path_var.get().strip())
        save_config(self.config)
        self._refresh_path_status()

    def _save_runtime_settings(self) -> None:
        try:
            recent_line_count = int(self.recent_line_var.get())
        except ValueError:
            recent_line_count = self.config.recent_line_count
            self.recent_line_var.set(str(recent_line_count))

        recent_line_count = max(500, min(20000, recent_line_count))
        self.recent_line_var.set(str(recent_line_count))
        analysis_mode = self.analysis_mode_var.get()
        if analysis_mode not in ANALYSIS_MODE_LABELS:
            analysis_mode = "local"
            self.analysis_mode_var.set(analysis_mode)

        self.config = replace(
            self.config,
            analysis_mode=analysis_mode,
            mask_sensitive_data=self.mask_var.get(),
            recent_line_count=recent_line_count,
        )
        save_config(self.config)
        self._update_mode_notice()

    def _update_mode_notice(self) -> None:
        mode = self.analysis_mode_var.get()
        if mode == "direct":
            self.mode_notice_var.set(
                "이 모드는 이 PC에서 직접 OpenAI API를 호출합니다. 전체 로그가 아니라 추출된 오류 블록과 메타정보만 AI 분석에 사용됩니다."
            )
        else:
            self.mode_notice_var.set(
                "이 모드는 로그를 외부로 보내지 않습니다. 로컬 규칙 기반 분석과 누적 히스토리 비교만 수행합니다."
            )

    def _refresh_path_status(self) -> None:
        path = self.path_var.get().strip()
        if not path:
            self.path_status_var.set("로그 파일 경로가 비어 있습니다.")
            return
        file_path = Path(path)
        if not file_path.exists():
            self.path_status_var.set("파일이 존재하지 않습니다.")
            return
        if file_path.suffix.lower() != ".log":
            self.path_status_var.set(".log 파일이 아니지만 분석은 계속할 수 있습니다.")
            return
        if file_path.stat().st_size == 0:
            self.path_status_var.set("파일은 존재하지만 현재 비어 있습니다.")
            return
        self.path_status_var.set("파일 확인됨")

    def _choose_file(self) -> None:
        selected = filedialog.askopenfilename(
            title="로그 파일 선택",
            initialfile="Player.log",
            filetypes=[("Log files", "*.log"), ("All files", "*.*")],
        )
        if selected:
            self.path_var.set(selected)

    def _restore_default_path(self) -> None:
        self.path_var.set(build_default_log_path())

    def _start_analysis(self) -> None:
        if self.is_analyzing:
            return

        path = self.path_var.get().strip()
        if not path:
            messagebox.showerror("경로 오류", "로그 파일 경로를 입력해 주세요.")
            return

        self._save_runtime_settings()
        self.is_analyzing = True
        self._set_busy_state(True)
        worker = threading.Thread(target=self._run_analysis, args=(path,), daemon=True)
        worker.start()

    def _run_analysis(self, path: str) -> None:
        try:
            self.result_queue.put(("status", "로그 파일 읽는 중"))
            snapshot = read_log_snapshot(path, self.config.recent_line_count)
            source_text = snapshot.text
            source_full_text = snapshot.full_text
            if self.config.mask_sensitive_data:
                source_text = mask_sensitive_text(source_text)
                source_full_text = mask_sensitive_text(source_full_text)
                snapshot = replace(
                    snapshot,
                    text=source_text,
                    lines=source_text.splitlines(),
                    full_text=source_full_text,
                    full_lines=source_full_text.splitlines(),
                )

            self.result_queue.put(("status", "오류 구간 추출 중"))
            blocks = extract_error_blocks(snapshot.lines)
            if len(blocks) <= 1 and snapshot.total_lines > snapshot.loaded_lines:
                blocks = extract_error_blocks(snapshot.full_lines)

            local_result = build_local_analysis(snapshot, blocks)
            similar_records = find_similar_history(blocks)
            history_summary = format_history_summary(similar_records)

            preview_chunks = []
            for block in blocks[:8]:
                preview_chunks.append(
                    f"[오류 블록 {block.index} | 줄 {block.start_line}-{block.end_line} | 반복 {block.occurrences}회]\n{block.text}"
                )
            preview = "\n\n".join(preview_chunks) if preview_chunks else snapshot.text[:8000]

            if not blocks:
                local_result.ai_status = "로컬 분석만 표시"
                self.result_queue.put(("done", (local_result, preview, history_summary)))
                return

            ai_comment = None
            ai_status = "로컬 분석만 표시"
            if self.config.analysis_mode == "direct":
                if not os.getenv("OPENAI_API_KEY"):
                    ai_status = "API 키가 없어 로컬 분석만 표시했습니다."
                else:
                    self.result_queue.put(("status", "이 PC에서 직접 AI 분석 중"))
                    ai_comment = request_direct_ai_comment(snapshot, blocks, local_result)
                    if ai_comment:
                        ai_status = "이 PC에서 직접 AI 분석 성공"
                    else:
                        ai_status = "직접 AI 분석에 실패해 로컬 분석만 표시했습니다."

            if ai_comment:
                local_result.ai_comment = ai_comment
                local_result.source = "local+ai"
            local_result.ai_status = ai_status

            try:
                append_history(
                    build_record(
                        log_path=snapshot.path,
                        analysis_mode=self.config.analysis_mode,
                        result=local_result,
                        blocks=blocks,
                    )
                )
            except OSError:
                history_summary += "\n\n히스토리 파일 저장에 실패했습니다."

            self.result_queue.put(("done", (local_result, preview, history_summary)))
        except FileNotFoundError:
            self.result_queue.put(("error", "로그 파일을 찾을 수 없습니다. 경로를 확인해 주세요."))
        except PermissionError:
            self.result_queue.put(("error", "로그 파일을 읽을 권한이 없습니다."))
        except UnicodeDecodeError:
            self.result_queue.put(("error", "지원하는 인코딩으로 로그 파일을 해석하지 못했습니다."))
        except Exception as exc:
            debug_trace = traceback.format_exc(limit=4)
            self.result_queue.put(("error", f"예상하지 못한 오류가 발생했습니다.\n{exc}\n\n{debug_trace}"))

    def _poll_queue(self) -> None:
        try:
            while True:
                kind, payload = self.result_queue.get_nowait()
                if kind == "status":
                    self.status_var.set(str(payload))
                elif kind == "done":
                    result, preview, history_summary = payload  # type: ignore[misc]
                    self._apply_result(result, preview, history_summary)
                    self._set_busy_state(False)
                    self.status_var.set("분석 완료")
                elif kind == "error":
                    self._set_busy_state(False)
                    self.status_var.set("분석 실패")
                    messagebox.showerror("분석 실패", str(payload))
        except queue.Empty:
            pass
        finally:
            self.root.after(150, self._poll_queue)

    def _set_busy_state(self, busy: bool) -> None:
        self.is_analyzing = busy
        state = "disabled" if busy else "normal"
        self.analyze_button.configure(state=state)
        self.browse_button.configure(state=state)
        self.restore_button.configure(state=state)
        self.path_entry.configure(state=state)
        self.recent_line_spinbox.configure(state=state)
        if busy:
            self.progress.start(10)
        else:
            self.progress.stop()

    def _apply_result(self, result: AnalysisResult, preview: str, history_summary: str) -> None:
        self.last_result = result
        self.last_log_preview = preview
        self.last_history_summary = history_summary

        summary = result.summary
        if result.ai_comment:
            summary = f"{summary}\n\n[AI 보강 분석]\n{result.ai_comment}"

        self._set_text(self.summary_text, summary)
        self._set_text(self.cause_text, "\n".join(f"- {line}" for line in result.causes))
        self._set_text(self.action_text, "\n".join(f"- {line}" for line in result.actions))
        self._set_text(self.followup_text, "\n".join(f"- {line}" for line in result.followups))
        self._set_text(
            self.meta_text,
            "\n".join(
                result.meta_lines
                + [
                    f"분석 모드: {ANALYSIS_MODE_LABELS.get(self.analysis_mode_var.get(), '알 수 없음')}",
                    f"AI 상태: {result.ai_status or '해당 없음'}",
                    f"확실도: {result.confidence}",
                ]
            ),
        )
        self._set_text(
            self.evidence_text,
            "\n".join(result.evidence) if result.evidence else "표시할 대표 근거가 없습니다.",
        )
        self._set_text(self.history_text, history_summary)
        self._set_text(self.preview_text, preview)
        self.copy_button.configure(state="normal")
        self.copy_log_button.configure(state="normal")

    def _set_text(self, widget: tk.Text, value: str) -> None:
        widget.configure(state="normal")
        widget.delete("1.0", "end")
        widget.insert("1.0", value)
        widget.configure(state="disabled")

    def _copy_result(self) -> None:
        if not self.last_result:
            return
        summary = self.summary_text.get("1.0", "end").strip()
        payload = "\n\n".join(
            [
                "[문제 요약]\n" + summary,
                "[가능성 높은 원인]\n" + "\n".join(self.last_result.causes),
                "[권장 조치]\n" + "\n".join(self.last_result.actions),
                "[추가 확인 항목]\n" + "\n".join(self.last_result.followups),
                "[누적 히스토리 비교]\n" + self.last_history_summary,
                "[근거 로그 요약]\n" + "\n".join(self.last_result.evidence),
            ]
        )
        self.root.clipboard_clear()
        self.root.clipboard_append(payload)
        self.status_var.set("분석 결과를 클립보드에 복사했습니다.")

    def _copy_log(self) -> None:
        if not self.last_log_preview:
            return
        self.root.clipboard_clear()
        self.root.clipboard_append(self.last_log_preview)
        self.status_var.set("근거 로그를 클립보드에 복사했습니다.")


def main() -> None:
    root = tk.Tk()
    AnalyzerApp(root)
    root.mainloop()
