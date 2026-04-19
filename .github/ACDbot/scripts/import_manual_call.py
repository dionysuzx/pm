#!/usr/bin/env python3
"""
Manual breakout call workflow.

This script intentionally models the real operator workflow as two commands:

1. prepare
   - normalize the local Zoom `chat.txt`
   - transcribe the local recording
   - run transcript cleanup + summary generation
   - write PM artifact metadata

2. publish
   - attach the final YouTube URL
   - regenerate the PM manifest

The design is deliberately strict:
- no issue/date/number/title args from the operator
- parent metadata is derived from PM's existing mapping
- breakout numbering is assigned automatically
- the only call-specific inputs are the source folder, breakout series, parent call,
  and later the YouTube URL
"""

from __future__ import annotations

import argparse
import json
import mimetypes
import os
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

import requests

SCRIPT_DIR = Path(__file__).parent
ACDBOT_DIR = SCRIPT_DIR.parent
PIPELINE_DIR = SCRIPT_DIR / "asset_pipeline"
ARTIFACTS_DIR = ACDBOT_DIR / "artifacts"
MAPPING_FILE = ACDBOT_DIR / "meeting_topic_mapping.json"

OPENAI_TRANSCRIPTIONS_URL = "https://api.openai.com/v1/audio/transcriptions"
OPENAI_TRANSCRIPTION_MODEL = "whisper-1"
MAX_TRANSCRIPTION_BYTES = 24 * 1024 * 1024
DATE_SUFFIX_RE = re.compile(r",\s*[A-Z][a-z]+\s+\d{1,2},\s+\d{4}$")
PARENT_RE = re.compile(r"^(?P<series>[a-z0-9-]+)/(?P<number>\d+)$")

BREAKOUT_DISPLAY_NAMES = {
    "epbs": "ePBS breakout",
    "bal": "BAL breakout",
    "focil": "FOCIL breakout",
    "pqi": "PQ Interop breakout",
}


@dataclass(frozen=True)
class ParentCall:
    series: str
    number: int
    issue: int
    date: str
    issue_title: str


def run_command(
    cmd: list[str],
    *,
    cwd: Path | None = None,
    capture_output: bool = False,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd,
        cwd=cwd,
        text=True,
        capture_output=capture_output,
        check=check,
    )


def load_mapping() -> dict:
    if not MAPPING_FILE.exists():
        raise SystemExit(f"Mapping file not found: {MAPPING_FILE}")
    with open(MAPPING_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def parse_parent(value: str) -> tuple[str, int]:
    match = PARENT_RE.match(value.strip())
    if not match:
        raise SystemExit(f"--parent must look like acdt/077, got: {value}")
    return match.group("series"), int(match.group("number"))


def occurrence_number_from_title(occurrence: dict) -> int | None:
    issue_title = occurrence.get("issue_title", "")
    match = re.search(r"#\s*(\d+)", issue_title)
    if match:
        return int(match.group(1))
    raw = occurrence.get("occurrence_number")
    return int(raw) if raw is not None else None


def resolve_parent_call(parent_series: str, parent_number: int) -> ParentCall:
    mapping = load_mapping()
    series_data = mapping.get(parent_series)
    if not series_data:
        raise SystemExit(f"Series '{parent_series}' not found in meeting_topic_mapping.json")

    for occurrence in series_data.get("occurrences", []):
        if occurrence_number_from_title(occurrence) != parent_number:
            continue
        issue = occurrence.get("issue_number")
        start_time = occurrence.get("start_time", "")
        issue_title = occurrence.get("issue_title", "")
        if not issue or not start_time or not issue_title:
            raise SystemExit(f"Parent call {parent_series}/{parent_number:03d} is missing required mapping metadata")
        return ParentCall(
            series=parent_series,
            number=parent_number,
            issue=int(issue),
            date=start_time.split("T")[0],
            issue_title=issue_title,
        )

    raise SystemExit(f"Could not resolve parent call {parent_series}/{parent_number:03d} from mapping")


def breakout_display_name(series: str) -> str:
    return BREAKOUT_DISPLAY_NAMES.get(series, f"{series.upper()} breakout")


def strip_date_suffix(title: str) -> str:
    return DATE_SUFFIX_RE.sub("", title).strip()


def breakout_meeting_title(parent: ParentCall, series: str) -> str:
    return f"{strip_date_suffix(parent.issue_title)} -- {breakout_display_name(series)}"


def artifact_dir(series: str, date: str, number: int) -> Path:
    return ARTIFACTS_DIR / series / f"{date}_{number:03d}"


def load_config(config_path: Path) -> dict:
    if not config_path.exists():
        return {}
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}


def find_existing_breakout(series: str, parent: ParentCall) -> int | None:
    series_dir = ARTIFACTS_DIR / series
    if not series_dir.exists():
        return None

    for call_dir in sorted(series_dir.iterdir()):
        if not call_dir.is_dir():
            continue
        config = load_config(call_dir / "config.json")
        parent_config = config.get("parent")
        if not isinstance(parent_config, dict):
            continue
        if (
            parent_config.get("series") == parent.series
            and int(parent_config.get("number", -1)) == parent.number
            and int(parent_config.get("issue", -1)) == parent.issue
        ):
            match = re.search(r"_(\d+)$", call_dir.name)
            if match:
                return int(match.group(1))
    return None


def next_breakout_number(series: str) -> int:
    series_dir = ARTIFACTS_DIR / series
    max_number = 0
    if not series_dir.exists():
        return 1

    for call_dir in series_dir.iterdir():
        if not call_dir.is_dir():
            continue
        match = re.search(r"_(\d+)$", call_dir.name)
        if match:
            max_number = max(max_number, int(match.group(1)))
    return max_number + 1


def breakout_number(series: str, parent: ParentCall) -> int:
    existing = find_existing_breakout(series, parent)
    if existing is not None:
        return existing
    return next_breakout_number(series)


def canonicalize_chat(source_dir: Path) -> str:
    chat_path = source_dir / "chat.txt"
    if not chat_path.exists():
        raise SystemExit(f"Expected chat.txt in source dir: {chat_path}")

    text = chat_path.read_text(encoding="utf-8", errors="replace")
    lines = text.splitlines()
    normalized: list[str] = []
    current_index: int | None = None

    for raw_line in lines:
        line = raw_line.rstrip()
        if not line.strip():
            continue

        canonical_match = re.match(r"^(\d{2}:\d{2}:\d{2})\t(.+?):\t([\s\S]*)$", line)
        export_match = re.match(r"^(\d{2}:\d{2}:\d{2})\t\s*From\s+(.+?)\s*:\s*([\s\S]*)$", line)
        match = canonical_match or export_match
        if match:
            timestamp, speaker, message = match.groups()
            normalized.append(f"{timestamp}\t{speaker.strip()}:\t{message.strip()}")
            current_index = len(normalized) - 1
            continue

        if current_index is not None:
            normalized[current_index] = f"{normalized[current_index]}\n{line.strip()}"

    if not normalized:
        raise SystemExit(f"Could not parse any chat messages from {chat_path}")

    return "\n".join(normalized).strip() + "\n"


def choose_media_source(source_dir: Path) -> Path:
    preferred_patterns = [
        "audio*.m4a",
        "audio*.mp3",
        "audio*.wav",
        "video*.mp4",
        "*.m4a",
        "*.mp3",
        "*.wav",
        "*.mp4",
    ]
    for pattern in preferred_patterns:
        matches = sorted(path for path in source_dir.glob(pattern) if path.is_file())
        if matches:
            return matches[0]
    raise SystemExit(f"Could not find an audio/video recording in {source_dir}")


def ffprobe_value(path: Path, entry: str) -> str:
    result = run_command(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            entry,
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(path),
        ],
        capture_output=True,
    )
    return result.stdout.strip()


def media_duration_seconds(path: Path) -> float:
    return float(ffprobe_value(path, "format=duration"))


def format_vtt_timestamp(seconds: float) -> str:
    millis = round(seconds * 1000)
    hours, remainder = divmod(millis, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    secs, ms = divmod(remainder, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}.{ms:03d}"


def split_for_transcription(media_path: Path, temp_dir: Path) -> list[Path]:
    size_bytes = media_path.stat().st_size
    if size_bytes <= MAX_TRANSCRIPTION_BYTES:
        return [media_path]

    duration = media_duration_seconds(media_path)
    estimated_seconds = max(300, int(duration * (MAX_TRANSCRIPTION_BYTES / size_bytes) * 0.85))
    output_pattern = temp_dir / "chunk_%03d.m4a"
    run_command(
        [
            "ffmpeg",
            "-v",
            "error",
            "-i",
            str(media_path),
            "-f",
            "segment",
            "-segment_time",
            str(estimated_seconds),
            "-c",
            "copy",
            str(output_pattern),
        ]
    )
    chunks = sorted(temp_dir.glob("chunk_*.m4a"))
    if not chunks:
        raise SystemExit("ffmpeg did not produce any transcription chunks")
    return chunks


def transcribe_chunk(path: Path) -> dict:
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise SystemExit("OPENAI_API_KEY is required for breakout transcript generation")

    mime_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    with open(path, "rb") as media_file:
        response = requests.post(
            OPENAI_TRANSCRIPTIONS_URL,
            headers={"Authorization": f"Bearer {api_key}"},
            data={
                "model": OPENAI_TRANSCRIPTION_MODEL,
                "response_format": "verbose_json",
            },
            files={"file": (path.name, media_file, mime_type)},
            timeout=1800,
        )

    if response.status_code != 200:
        raise SystemExit(
            f"OpenAI transcription failed for {path.name}: "
            f"{response.status_code} {response.text}"
        )

    return response.json()


def build_vtt_from_segments(segments: list[dict]) -> str:
    lines = ["WEBVTT", ""]
    for segment in segments:
        text = (segment.get("text") or "").strip()
        if not text:
            continue
        start = format_vtt_timestamp(float(segment["start"]))
        end = format_vtt_timestamp(float(segment["end"]))
        lines.append(f"{start} --> {end}")
        lines.append(text)
        lines.append("")
    return "\n".join(lines).strip() + "\n"


def transcribe_media(media_path: Path) -> str:
    all_segments: list[dict] = []
    with tempfile.TemporaryDirectory(prefix="breakout-transcribe-") as temp_dir_raw:
        temp_dir = Path(temp_dir_raw)
        chunks = split_for_transcription(media_path, temp_dir)
        offset = 0.0

        for index, chunk in enumerate(chunks, start=1):
            print(f"Transcribing chunk {index}/{len(chunks)}: {chunk.name}")
            result = transcribe_chunk(chunk)
            segments = result.get("segments", [])
            for segment in segments:
                adjusted = dict(segment)
                adjusted["start"] = float(segment["start"]) + offset
                adjusted["end"] = float(segment["end"]) + offset
                all_segments.append(adjusted)
            offset += media_duration_seconds(chunk)

    if not all_segments:
        raise SystemExit("Transcription completed but returned no segments")
    return build_vtt_from_segments(all_segments)


def write_file(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def run_pipeline(meeting_dir: Path, series: str, number: int) -> None:
    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise SystemExit("ANTHROPIC_API_KEY is required for changelog + summary generation")

    commands = [
        ["generate_changelog.py", "--call", series, "--number", str(number)],
        ["apply_changelog.py", "--call", series, "--number", str(number)],
        ["generate_summary.py", "--call", series, "--number", str(number)],
    ]

    print("\nRunning transcript cleanup + summary pipeline...")
    generate_cmd = [sys.executable, *commands[0]]
    result = run_command(generate_cmd, cwd=PIPELINE_DIR, check=False)
    if result.returncode != 0:
        raise SystemExit("generate_changelog.py failed")

    changelog_path = meeting_dir / "transcript_changelog.tsv"
    print(f"\nReview the generated changelog, then press Enter to continue:\n  {changelog_path}")
    input()

    for script_cmd in commands[1:]:
        full_cmd = [sys.executable, *script_cmd]
        result = run_command(full_cmd, cwd=PIPELINE_DIR, check=False)
        if result.returncode != 0:
            raise SystemExit(f"{script_cmd[0]} failed")


def regenerate_manifest() -> None:
    result = run_command([sys.executable, "generate_manifest.py"], cwd=PIPELINE_DIR, check=False)
    if result.returncode != 0:
        raise SystemExit("generate_manifest.py failed")


def base_config(parent: ParentCall, series: str) -> dict:
    return {
        "name": breakout_display_name(series),
        "meetingTitle": breakout_meeting_title(parent, series),
        "agendaIssue": parent.issue,
        "parent": {
            "series": parent.series,
            "number": parent.number,
            "issue": parent.issue,
        },
        "sync": {
            "transcriptStartTime": None,
            "videoStartTime": None,
        },
    }


def prepare_breakout(source_dir: Path, series: str, parent: ParentCall) -> Path:
    number = breakout_number(series, parent)
    meeting_dir = artifact_dir(series, parent.date, number)
    config_path = meeting_dir / "config.json"

    meeting_dir.mkdir(parents=True, exist_ok=True)

    print(f"Preparing {series} breakout #{number:03d}")
    print(f"Parent call: {parent.series}/{parent.number:03d} (issue #{parent.issue})")
    print(f"Artifact dir: {meeting_dir}")
    print(f"Expected YouTube title: {breakout_meeting_title(parent, series)}")

    chat_content = canonicalize_chat(source_dir)
    write_file(meeting_dir / "chat.txt", chat_content)

    media_source = choose_media_source(source_dir)
    print(f"Using media source: {media_source.name}")
    transcript_vtt = transcribe_media(media_source)
    write_file(meeting_dir / "transcript.vtt", transcript_vtt)

    config = load_config(config_path)
    merged_config = {
        **config,
        **base_config(parent, series),
        "sync": config.get("sync") or base_config(parent, series)["sync"],
    }
    write_file(config_path, json.dumps(merged_config, indent=2) + "\n")

    run_pipeline(meeting_dir, series, number)
    regenerate_manifest()
    return meeting_dir


def extract_youtube_video_id(url: str) -> str:
    match = re.search(r"(?:v=|youtu\.be/)([A-Za-z0-9_-]{6,})", url)
    if not match:
        raise SystemExit(f"Could not extract YouTube video ID from URL: {url}")
    return match.group(1)


def publish_breakout(series: str, parent: ParentCall, youtube_url: str) -> Path:
    number = breakout_number(series, parent)
    meeting_dir = artifact_dir(series, parent.date, number)
    config_path = meeting_dir / "config.json"
    if not config_path.exists():
        raise SystemExit(
            f"Prepared breakout artifacts not found for {series} + {parent.series}/{parent.number:03d}. "
            f"Run 'prepare' first."
        )

    config = load_config(config_path)
    config["videoUrl"] = f"https://www.youtube.com/watch?v={extract_youtube_video_id(youtube_url)}"
    write_file(config_path, json.dumps(config, indent=2) + "\n")
    regenerate_manifest()

    print(f"Published {series} breakout #{number:03d}")
    print(f"Artifact dir: {meeting_dir}")
    print(f"Video URL: {config['videoUrl']}")
    return meeting_dir


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Prepare or publish a manually recorded breakout call.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    prepare = subparsers.add_parser("prepare", help="Normalize chat, transcribe media, and run the PM pipeline")
    prepare.add_argument("--source-dir", required=True, help="Folder containing the local Zoom breakout export")
    prepare.add_argument(
        "--series",
        required=True,
        choices=sorted(BREAKOUT_DISPLAY_NAMES.keys()),
        help="Breakout series to publish (e.g. epbs or bal)",
    )
    prepare.add_argument("--parent", required=True, help="Parent call in <series>/<number> form, e.g. acdt/077")

    publish = subparsers.add_parser("publish", help="Attach the final YouTube URL and regenerate the PM manifest")
    publish.add_argument(
        "--series",
        required=True,
        choices=sorted(BREAKOUT_DISPLAY_NAMES.keys()),
        help="Breakout series to publish (e.g. epbs or bal)",
    )
    publish.add_argument("--parent", required=True, help="Parent call in <series>/<number> form, e.g. acdt/077")
    publish.add_argument("--youtube-url", required=True, help="Final YouTube URL for the uploaded breakout recording")

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    parent_series, parent_number = parse_parent(args.parent)
    parent = resolve_parent_call(parent_series, parent_number)

    if args.command == "prepare":
        source_dir = Path(os.path.expanduser(args.source_dir)).resolve()
        if not source_dir.exists():
            raise SystemExit(f"Source dir not found: {source_dir}")
        meeting_dir = prepare_breakout(source_dir, args.series, parent)
        print("\nPrepare complete.")
        print(f"Next step: upload to YouTube, then run:\n  uv run scripts/import_manual_call.py publish --series {args.series} --parent {args.parent} --youtube-url <url>")
        print(f"Prepared artifacts: {meeting_dir}")
        return

    meeting_dir = publish_breakout(args.series, parent, args.youtube_url)
    print("\nPublish complete.")
    print("Next step: commit the PM artifact changes and push them to your PM fork.")
    print(f"Published artifacts: {meeting_dir}")


if __name__ == "__main__":
    main()
