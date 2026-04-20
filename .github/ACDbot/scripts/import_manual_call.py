#!/usr/bin/env python3
"""
Manual breakout call workflow.

Split between a fast local step and a CI step:

1. init  (local, no API keys)
   - canonicalize the local Zoom `chat.txt`
   - write `config.json` with parent metadata and the operator-provided YouTube URL
   - regenerate `artifacts/manifest.json`

2. transcribe-pending  (CI, needs OPENAI + ANTHROPIC)
   - find any breakout artifacts dir whose config has a `parent` + `videoUrl` and no `transcript.vtt`
   - pull audio from the YouTube URL via yt-dlp
   - run transcribe → changelog → apply changelog → summary
   - regenerate `artifacts/manifest.json`

Design constraints:
- operator never waits on OpenAI/Anthropic transcription
- operator never commits raw .m4a / .mp4 recordings
- parent metadata is derived from PM's existing mapping
- breakout numbering is assigned automatically
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

from manual_call_chat import canonicalize_chat
from manual_call_media import download_youtube_audio, normalize_youtube_url, transcribe_media
from manual_call_series import (
    BREAKOUT_DISPLAY_NAMES,
    breakout_display_name,
    infer_breakout_series,
    strip_date_suffix,
)

SCRIPT_DIR = Path(__file__).parent
ACDBOT_DIR = SCRIPT_DIR.parent
PIPELINE_DIR = SCRIPT_DIR / "asset_pipeline"
ARTIFACTS_DIR = ACDBOT_DIR / "artifacts"
MAPPING_FILE = ACDBOT_DIR / "meeting_topic_mapping.json"

PARENT_RE = re.compile(r"^(?P<series>[a-z0-9-]+)/(?P<number>\d+)$")


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


def write_file(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def run_pipeline(meeting_dir: Path, series: str, number: int, *, non_interactive: bool = False) -> None:
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

    if not non_interactive:
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


def resolve_source_dir() -> Path:
    source_dir = Path.cwd().resolve()
    if not source_dir.exists():
        raise SystemExit(f"Current working directory not found: {source_dir}")
    if not (source_dir / "chat.txt").exists():
        raise SystemExit(
            f"Expected chat.txt in the current working directory: {source_dir}\n"
            "Run `init` from the breakout export folder."
        )
    return source_dir


def resolve_breakout_series(source_dir: Path, requested_series: str | None) -> str:
    if requested_series:
        return requested_series

    inferred_series = infer_breakout_series(source_dir)
    if inferred_series:
        return inferred_series

    supported = ", ".join(sorted(BREAKOUT_DISPLAY_NAMES))
    raise SystemExit(
        "Could not infer breakout series from the current directory name.\n"
        f"Pass --series explicitly ({supported})."
    )


def init_breakout(source_dir: Path, series: str, parent: ParentCall, youtube_url: str) -> Path:
    number = breakout_number(series, parent)
    meeting_dir = artifact_dir(series, parent.date, number)
    config_path = meeting_dir / "config.json"

    meeting_dir.mkdir(parents=True, exist_ok=True)

    print(f"Initializing {series} breakout #{number:03d}")
    print(f"Parent call: {parent.series}/{parent.number:03d} (issue #{parent.issue})")
    print(f"Artifact dir: {meeting_dir}")

    chat_content = canonicalize_chat(source_dir)
    write_file(meeting_dir / "chat.txt", chat_content)

    normalized_url = normalize_youtube_url(youtube_url)

    config = load_config(config_path)
    merged_config = {
        **config,
        **base_config(parent, series),
        "videoUrl": normalized_url,
        "sync": config.get("sync") or base_config(parent, series)["sync"],
    }
    write_file(config_path, json.dumps(merged_config, indent=2) + "\n")

    regenerate_manifest()
    return meeting_dir


def parse_call_dir(call_dir: Path) -> tuple[str, int] | None:
    match = re.search(r"_(\d+)$", call_dir.name)
    if not match:
        return None
    return call_dir.parent.name, int(match.group(1))


def find_pending_breakouts() -> list[tuple[Path, str, int]]:
    """Return [(meeting_dir, series, number)] for breakouts missing a transcript."""
    pending: list[tuple[Path, str, int]] = []
    if not ARTIFACTS_DIR.exists():
        return pending
    for series_dir in sorted(ARTIFACTS_DIR.iterdir()):
        if not series_dir.is_dir():
            continue
        for call_dir in sorted(series_dir.iterdir()):
            if not call_dir.is_dir():
                continue
            config = load_config(call_dir / "config.json")
            parent_config = config.get("parent")
            if not isinstance(parent_config, dict):
                continue
            if not config.get("videoUrl"):
                continue
            if (call_dir / "transcript.vtt").exists():
                continue
            parsed = parse_call_dir(call_dir)
            if not parsed:
                continue
            pending.append((call_dir, parsed[0], parsed[1]))
    return pending


def transcribe_breakout(meeting_dir: Path, series: str, number: int) -> None:
    config = load_config(meeting_dir / "config.json")
    video_url = config.get("videoUrl")
    if not video_url:
        raise SystemExit(f"{meeting_dir}: config.json missing videoUrl")

    print(f"\n=== Transcribing {series}/#{number:03d} ===")
    print(f"  dir:   {meeting_dir}")
    print(f"  video: {video_url}")

    with tempfile.TemporaryDirectory(prefix="breakout-yt-") as temp_dir_raw:
        audio_path = download_youtube_audio(video_url, Path(temp_dir_raw))
        print(f"  audio: {audio_path.name} ({audio_path.stat().st_size} bytes)")
        transcript_vtt = transcribe_media(audio_path)

    write_file(meeting_dir / "transcript.vtt", transcript_vtt)
    run_pipeline(meeting_dir, series, number, non_interactive=True)


def transcribe_pending() -> int:
    if not os.environ.get("OPENAI_API_KEY"):
        raise SystemExit("OPENAI_API_KEY is required")
    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise SystemExit("ANTHROPIC_API_KEY is required")

    pending = find_pending_breakouts()
    if not pending:
        print("No pending breakouts to transcribe.")
        return 0

    print(f"Found {len(pending)} breakout(s) needing transcription.")
    for meeting_dir, series, number in pending:
        transcribe_breakout(meeting_dir, series, number)

    regenerate_manifest()
    return len(pending)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Breakout call ingest helpers.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    init = subparsers.add_parser(
        "init",
        help="Write config.json + chat.txt for a breakout in the current directory. Local, no API keys.",
    )
    init.add_argument(
        "--series",
        choices=sorted(BREAKOUT_DISPLAY_NAMES.keys()),
        help="Breakout series when it cannot be inferred from the current directory name",
    )
    init.add_argument("--parent", required=True, help="Parent call in <series>/<number> form, e.g. acdt/077")
    init.add_argument("--youtube-url", required=True, help="YouTube URL for the uploaded breakout recording")

    subparsers.add_parser(
        "transcribe-pending",
        help="Transcribe any breakouts with a videoUrl but no transcript.vtt. CI step.",
    )

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    if args.command == "init":
        parent_series, parent_number = parse_parent(args.parent)
        parent = resolve_parent_call(parent_series, parent_number)
        source_dir = resolve_source_dir()
        series = resolve_breakout_series(source_dir, args.series)
        meeting_dir = init_breakout(source_dir, series, parent, args.youtube_url)
        print("\nInit complete.")
        print(f"Artifacts: {meeting_dir}")
        print("Next step: commit the PM artifact changes and push them.")
        print("CI will transcribe the video and append the derived artifacts automatically.")
        return

    transcribe_pending()


if __name__ == "__main__":
    main()
