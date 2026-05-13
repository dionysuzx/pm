from __future__ import annotations

import mimetypes
import os
import subprocess
import tempfile
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import requests

OPENAI_TRANSCRIPTIONS_URL = "https://api.openai.com/v1/audio/transcriptions"
OPENAI_TRANSCRIPTION_MODEL = "whisper-1"
MAX_TRANSCRIPTION_BYTES = 24 * 1024 * 1024


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


def extract_youtube_video_id(url: str) -> str:
    parsed = urlparse(url)
    hostname = (parsed.hostname or "").lower()
    if hostname in {"youtube.com", "www.youtube.com", "m.youtube.com"}:
        video_ids = parse_qs(parsed.query).get("v")
        if video_ids:
            return video_ids[0]
    if hostname == "youtu.be":
        video_id = parsed.path.lstrip("/")
        if video_id:
            return video_id
    raise SystemExit(f"Could not extract YouTube video ID from URL: {url}")


def normalize_youtube_url(url: str) -> str:
    return f"https://www.youtube.com/watch?v={extract_youtube_video_id(url)}"


def download_youtube_audio(video_url: str, dest_dir: Path) -> Path:
    dest_dir.mkdir(parents=True, exist_ok=True)
    output_template = str(dest_dir / "audio.%(ext)s")
    run_command(
        [
            "yt-dlp",
            "--quiet",
            "--no-warnings",
            "-f",
            "bestaudio[ext=m4a]/bestaudio",
            "-x",
            "--audio-format",
            "m4a",
            "-o",
            output_template,
            video_url,
        ]
    )
    candidates = sorted(dest_dir.glob("audio.*"))
    if not candidates:
        raise SystemExit(f"yt-dlp produced no audio file for {video_url}")
    return candidates[0]


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
