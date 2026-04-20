from __future__ import annotations

import re
from pathlib import Path


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
