from __future__ import annotations

import argparse
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.log_parser import ERROR_PATTERN, PRIMARY_ENCODINGS
from src.slack_report import ISSUE_RULES, _is_noise_line, _match_rule, _normalize_signature


SUPPORTED_SUFFIXES = {".log", ".txt"}


@dataclass
class Bucket:
    count: int = 0
    files: set[str] = field(default_factory=set)
    sample: str = ""
    sample_path: str = ""
    first_line: int = 0


def _decode_bytes(raw: bytes) -> tuple[str, str]:
    last_error: Exception | None = None
    for encoding in PRIMARY_ENCODINGS:
        try:
            return raw.decode(encoding), encoding
        except UnicodeDecodeError as exc:
            last_error = exc
    raise UnicodeDecodeError("unknown", b"", 0, 1, str(last_error))


def _iter_log_files(root: Path) -> Iterable[Path]:
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        if path.suffix.lower() not in SUPPORTED_SUFFIXES:
            continue
        yield path


def _update_bucket(
    table: dict[str, Bucket],
    key: str,
    *,
    file_name: str,
    sample: str,
    line_number: int,
) -> None:
    bucket = table.get(key)
    if bucket is None:
        table[key] = Bucket(
            count=1,
            files={file_name},
            sample=sample,
            sample_path=file_name,
            first_line=line_number,
        )
        return
    bucket.count += 1
    bucket.files.add(file_name)


def _rank_items(table: dict[str, Bucket], *, limit: int) -> list[tuple[str, Bucket]]:
    return sorted(
        table.items(),
        key=lambda item: (-item[1].count, -len(item[1].files), item[1].sample.lower()),
    )[:limit]


def build_report(root: Path) -> str:
    total_files = 0
    total_lines = 0
    total_error_candidates = 0
    matched_count = 0
    unmatched_count = 0
    noise_count = 0

    per_rule: dict[str, Bucket] = defaultdict(Bucket)
    unmatched: dict[str, Bucket] = {}
    noise: dict[str, Bucket] = {}
    decode_failures: list[str] = []

    for path in _iter_log_files(root):
        total_files += 1
        try:
            decoded, _encoding = _decode_bytes(path.read_bytes())
        except Exception as exc:  # pragma: no cover - diagnostic path
            decode_failures.append(f"{path.name}: {exc}")
            continue

        for line_number, raw_line in enumerate(decoded.splitlines(), start=1):
            total_lines += 1
            line = raw_line.strip()
            if not line or not ERROR_PATTERN.search(line):
                continue
            total_error_candidates += 1

            if _is_noise_line(line):
                noise_count += 1
                _update_bucket(
                    noise,
                    _normalize_signature(line),
                    file_name=path.name,
                    sample=line,
                    line_number=line_number,
                )
                continue

            rule = _match_rule(line)
            if rule is not None:
                matched_count += 1
                _update_bucket(
                    per_rule,
                    rule.key,
                    file_name=path.name,
                    sample=line,
                    line_number=line_number,
                )
                continue

            unmatched_count += 1
            _update_bucket(
                unmatched,
                _normalize_signature(line),
                file_name=path.name,
                sample=line,
                line_number=line_number,
            )

    title_map = {rule.key: rule.title for rule in ISSUE_RULES}

    lines = [
        "# Log Rule Scan Report",
        "",
        f"- Scan root: `{root}`",
        f"- Files scanned: {total_files}",
        f"- Total lines scanned: {total_lines:,}",
        f"- Error candidates: {total_error_candidates:,}",
        f"- Matched by current rules: {matched_count:,}",
        f"- Unmatched candidates: {unmatched_count:,}",
        f"- Noise candidates excluded: {noise_count:,}",
        "",
        "## Current Rule Coverage",
        "",
    ]

    if per_rule:
        for key, bucket in _rank_items(dict(per_rule), limit=20):
            lines.append(
                f"- {title_map.get(key, key)}: {bucket.count} hits across {len(bucket.files)} files"
            )
            lines.append(f"  - Example: `{bucket.sample}`")
    else:
        lines.append("- No current rule matches found.")

    lines.extend(["", "## New Rule Candidates", ""])
    if unmatched:
        for signature, bucket in _rank_items(unmatched, limit=40):
            lines.append(
                f"- {bucket.count} hits across {len(bucket.files)} files"
            )
            lines.append(f"  - Example: `{bucket.sample}`")
            lines.append(f"  - First seen: `{bucket.sample_path}:{bucket.first_line}`")
            lines.append(f"  - Signature: `{signature}`")
    else:
        lines.append("- No unmatched candidates found.")

    lines.extend(["", "## Noise Candidates", ""])
    if noise:
        for _signature, bucket in _rank_items(noise, limit=20):
            lines.append(
                f"- {bucket.count} hits across {len(bucket.files)} files"
            )
            lines.append(f"  - Example: `{bucket.sample}`")
    else:
        lines.append("- No noise candidates found.")

    if decode_failures:
        lines.extend(["", "## Decode Failures", ""])
        for item in decode_failures:
            lines.append(f"- {item}")

    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description="Scan log files and summarize current rule coverage.")
    parser.add_argument("root", nargs="?", default=".", help="Folder containing log files")
    parser.add_argument(
        "--output",
        default="data/log-rule-scan-report.md",
        help="Path to write the markdown report",
    )
    args = parser.parse_args()

    root = Path(args.root).expanduser().resolve()
    output = Path(args.output).expanduser().resolve()

    report = build_report(root)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(report, encoding="utf-8")
    print(f"Wrote report to {output}")


if __name__ == "__main__":
    main()
