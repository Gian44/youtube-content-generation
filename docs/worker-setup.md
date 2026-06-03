# Worker Setup Guide

## Overview

The StoryFactory worker is a Python application that handles:
- Story generation (AI-powered)
- TTS audio generation
- Caption alignment
- Background asset collection
- Video rendering (FFmpeg)
- YouTube uploading
- Analytics collection

In the finished product, the worker runs **inside the desktop app** (`apps/desktop`), which also embeds the dashboard and runs the daily scheduler. During development you can invoke the same commands from the repo root via `npm run worker:*`.

See [desktop-app.md](desktop-app.md) for packaging and local runtime layout.

## Prerequisites

- Python 3.11+
- FFmpeg installed and available in PATH
- API keys configured in `.env`

### Installing FFmpeg

**Windows:**
```bash
# Using winget
winget install FFmpeg

# Or download from https://ffmpeg.org/download.html
```

**macOS:**
```bash
brew install ffmpeg
```

**Linux:**
```bash
sudo apt install ffmpeg
```

## Local Setup

### 1. Install Dependencies

```bash
cd apps/worker
pip install -r requirements.txt
```

### 2. Initialize Database

```bash
# From project root
npm run db:migrate
npm run seed
```

### 3. Test with Dry Run

```bash
npm run worker:daily -- --dry-run
```

This runs the complete pipeline with placeholder data — no API calls, no uploads.

### 4. Run Daily Pipeline

```bash
npm run worker:daily
```

### 5. Individual Commands

```bash
npm run worker:render     # Process render queue
npm run worker:upload     # Upload to YouTube
npm run worker:analytics  # Fetch analytics
```

## Scheduled runs

**Recommended:** use the **desktop app** built-in scheduler (`apps/desktop/scheduler.js`), which runs:

```bash
python -m storyfactory full --all-active
```

**CLI / cron** (optional, for development without Electron):

**All active channels** (recommended for multi-channel):

```bash
npm run worker:full -- --all-active
```

**One channel:**

```bash
npm run worker:full -- --channel <slug>
```

**Cron example** (Linux/Mac, daily at 6:00):

```cron
0 6 * * * cd /path/to/storyfactory && npm run worker:full -- --all-active
```

The desktop app runs the same `full --all-active` command on its built-in schedule. See [multi-channel.md](multi-channel.md).

## Monitoring

The worker logs to:
- Console (rich formatted output)
- `./logs/` directory (structured JSON)

Check status:
```bash
# View recent logs
tail -f logs/worker.log

# Check batch status via dashboard
open http://localhost:3000
```

## Troubleshooting

### "FFmpeg not found"
Ensure FFmpeg is installed and in your PATH:
```bash
ffmpeg -version
```

### "OpenAI API key not configured"
Copy `.env.example` to `.env` and add your API key.

### "No stories passed policy checks"
The policy checker may be too strict. Check the flagged stories in the dashboard and adjust sensitivity.

### High memory usage during rendering
Reduce concurrent renders or add `--batch-size 1` flag.
