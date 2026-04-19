# Breakout Workflow

This is the operator workflow for breakout rooms that do not have Zoom cloud assets.

Transcription happens in CI. The operator's local step is fast and needs no API keys.

1. Upload the breakout recording to YouTube manually.
2. Run `init` locally with the YouTube URL. Writes `config.json` + canonicalized `chat.txt`.
3. Commit the PM changes and push.
4. CI (`breakout-transcription.yml`) pulls audio from YouTube, transcribes, runs changelog + summary, commits derived artifacts, and dispatches Forkcast.

Only derived artifacts belong in the repo:
- `chat.txt`
- `transcript.vtt`
- `transcript_changelog.tsv`
- `transcript_corrected.vtt`
- `tldr.json`
- `config.json`
- `manifest.json`

Do not commit raw `.m4a` / `.mp4` recordings.

## Prerequisites

Local machine requirements:
- `uv`
- No API keys required for local steps.

The CI workflow uses:
- `yt-dlp` + OpenAI Whisper to transcribe the uploaded YouTube video
- the existing PM cleanup pipeline for changelog, corrected transcript, and summary

## Required local folder shape

The source folder must contain `chat.txt`. Everything else is ignored by the local step.

Example (standard Zoom breakout export):

```text
~/Downloads/drive-download-.../2026-04-13 ... -- EPBS BREAKOUT/
├── audio1761632020.m4a        # uploaded to YouTube manually
├── chat.txt                   # the canonical chat source
├── meeting_saved_new_chat.txt # ignored
├── recording.conf             # ignored
└── video1761632020.mp4        # uploaded to YouTube manually
```

## Step 1: Upload the video to YouTube

Upload the recording. Any title is fine — the pipeline does not parse it. `unlisted` is sufficient.

Grab the final URL (e.g. `https://www.youtube.com/watch?v=abc123`).

## Step 2: Run `init` locally

Example for the ePBS breakout from `acdt/077`:

```bash
cd /Users/lucy/fun/pm/.github/ACDbot

uv run scripts/import_manual_call.py init \
  --series epbs \
  --parent acdt/077 \
  --source-dir "/Users/lucy/Downloads/drive-download-.../2026-04-13 ... -- EPBS BREAKOUT" \
  --youtube-url "https://www.youtube.com/watch?v=REPLACE_ME"
```

What `init` does:
- resolves the parent call from PM metadata
- assigns the next breakout number automatically
- normalizes the local `chat.txt` into PM's canonical chat format
- writes `config.json` (including `videoUrl`, `parent`)
- regenerates `artifacts/manifest.json`

No transcription, no API calls. Runs in seconds.

## Step 3: Commit and push PM changes

```bash
cd /Users/lucy/fun/pm

git add .github/ACDbot/artifacts
git commit -m "init <series> breakout for <parent>"
git push fork HEAD
```

## Step 4: CI picks it up

Once the PM changes land on `master`:

1. `dispatch-forkcast-on-assets.yml` fires → Forkcast syncs the call with chat + video (no transcript yet).
2. `breakout-transcription.yml` fires in parallel → downloads audio via `yt-dlp`, transcribes, runs changelog + summary, commits the derived artifacts to `master`, then dispatches Forkcast again.
3. Forkcast re-syncs and picks up the transcript + summary.

Both CI runs are automatic after merge. If the YouTube upload is still processing when CI runs and `yt-dlp` fails, just re-run the `Breakout Transcription` workflow from the Actions tab.
