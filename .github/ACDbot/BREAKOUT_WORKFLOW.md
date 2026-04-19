# Breakout Workflow

This is the operator workflow for breakout rooms that do not have Zoom cloud assets.

The design is intentionally simple:

1. `prepare` does all local processing.
2. You upload the video to YouTube manually.
3. `publish` attaches the final YouTube URL.
4. You commit the derived PM artifacts and push them.
5. After the PM change lands on `master`, Forkcast syncs automatically.

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
- `ffmpeg`
- `ffprobe`
- `OPENAI_API_KEY`
- `ANTHROPIC_API_KEY`

The script uses:
- OpenAI for first-pass transcription from the local recording
- the existing PM cleanup pipeline for changelog, corrected transcript, and summary

## Required local folder shape

The source folder must contain:
- `chat.txt`
- `audio*.m4a` or `video*.mp4`

`meeting_saved_new_chat.txt` is ignored. The canonical source is `chat.txt`.

Example:

```text
~/Downloads/drive-download-.../2026-04-13 ... -- EPBS BREAKOUT/
├── audio1761632020.m4a
├── chat.txt
├── meeting_saved_new_chat.txt   # ignored
├── recording.conf
└── video1761632020.mp4
```

## Step 1: Prepare the breakout locally

Example for the ePBS breakout from `acdt/077`:

```bash
cd /Users/lucy/fun/pm/.github/ACDbot

uv run scripts/import_manual_call.py prepare \
  --series epbs \
  --parent acdt/077 \
  --source-dir "/Users/lucy/Downloads/drive-download-20260419T212033Z-3-001/2026-04-13 16.02.32 All Core Devs - Testing (ACDT) #77, April 13, 2026 -- EPBS BREAKOUT"
```

Example for the BAL breakout from `acdt/077`:

```bash
cd /Users/lucy/fun/pm/.github/ACDbot

uv run scripts/import_manual_call.py prepare \
  --series bal \
  --parent acdt/077 \
  --source-dir "/Users/lucy/Downloads/drive-download-20260419T212033Z-3-001/2026-04-13 10.31.47 All Core Devs - Testing (ACDT) #77, April 13, 2026 -- BAL BREAKOUT"
```

What `prepare` does:
- resolves the parent call from PM metadata
- assigns the next breakout number automatically
- normalizes the local `chat.txt` into PM's canonical chat format
- transcribes the local recording
- generates `transcript_changelog.tsv`
- pauses for changelog review
- generates `transcript_corrected.vtt`
- generates `tldr.json`
- regenerates `artifacts/manifest.json`

`prepare` also prints the expected YouTube title. Use that exact title when uploading.

For `acdt/077` examples:
- `All Core Devs - Testing (ACDT) #77 -- ePBS breakout`
- `All Core Devs - Testing (ACDT) #77 -- BAL breakout`

## Step 2: Upload the video to YouTube manually

Upload the local recording with the title printed by `prepare`.

This is still a manual step.

## Step 3: Publish the final YouTube URL into PM metadata

After the upload exists:

```bash
cd /Users/lucy/fun/pm/.github/ACDbot

uv run scripts/import_manual_call.py publish \
  --series epbs \
  --parent acdt/077 \
  --youtube-url "https://www.youtube.com/watch?v=REPLACE_ME"
```

Or:

```bash
cd /Users/lucy/fun/pm/.github/ACDbot

uv run scripts/import_manual_call.py publish \
  --series bal \
  --parent acdt/077 \
  --youtube-url "https://www.youtube.com/watch?v=REPLACE_ME"
```

`publish` only:
- updates `config.json` with the final `videoUrl`
- regenerates `artifacts/manifest.json`

## Step 4: Commit and push PM changes

After `prepare` and `publish` are both done:

```bash
cd /Users/lucy/fun/pm

git add .github/ACDbot/artifacts .github/ACDbot/scripts .github/workflows
git commit -m "support manual breakout call ingest"
git push fork HEAD
```

## Step 5: Forkcast pickup

Once the PM changes land on `master`:
- PM dispatches a `pm-assets-updated` event to Forkcast
- Forkcast's existing sync workflow fetches the PM manifest and artifacts
- Forkcast commits the synced assets and redeploys

That final Forkcast sync is automatic after merge.
