# StoryFactory

> Local **desktop app** for fully automated, **multi-channel** YouTube content generation — original fictional Reddit-style stories

StoryFactory ships as an Electron application on your machine (`apps/desktop`). The dashboard and Python worker run locally; nothing is deployed to the cloud.

StoryFactory runs any number of **channels** — each an independent content operation with its own niche, content rules, prompt/voice customization, integrations, and YouTube account — all sharing one pipeline. Per channel it generates **1 long-form video** and **3–5 YouTube Shorts** per day, each featuring original fictional stories inspired by popular Reddit-style formats (AITA, relationships, revenge, etc.).

> **New to multi-channel?** See [docs/multi-channel.md](docs/multi-channel.md). Existing single-channel installs migrate automatically to a "default" channel — just run `npm run db:migrate`. **Scope:** local desktop only — [docs/project-scope.md](docs/project-scope.md).

> **Important**: All stories are original fiction. We do not scrape Reddit. We do not copy real posts. Every video description includes a configurable disclosure: *"These are original fictional stories created for entertainment."*

---

## Architecture

```
storyfactory/
├── apps/
│   ├── desktop/      # Electron app (target product — bundles dashboard + worker)
│   ├── dashboard/    # Next.js UI (embedded in desktop, dev via npm run dev)
│   └── worker/       # Python – FFmpeg rendering, TTS, captions, uploads
├── packages/
│   └── shared/       # Shared TypeScript types and Zod schemas
├── docs/             # Documentation
├── .env.example
└── package.json      # Root scripts (dashboard, worker, tests)
```

## Quick Start

### Prerequisites

- **Node.js** ≥ 18
- **Python** ≥ 3.11
- **FFmpeg** (install from https://ffmpeg.org)
- **API Keys** (see below)

### 1. Clone & Install

```bash
git clone <repo-url>
cd storyfactory

# Install Node dependencies (dashboard + shared)
npm install

# Install Python dependencies (worker)
cd apps/worker
pip install -r requirements.txt
cd ../..
```

### 2. Configure Environment

```bash
cp .env.example .env
# Edit .env with your API keys
```

**Required API keys:**
| Key | Where to get it | Required? |
|-----|----------------|-----------|
| `OPENAI_API_KEY` | https://platform.openai.com/api-keys | Yes |
| `GEMINI_API_KEY` | https://aistudio.google.com/apikey | Optional |
| `YOUTUBE_CLIENT_ID` | Google Cloud Console | For uploads |
| `YOUTUBE_CLIENT_SECRET` | Google Cloud Console | For uploads |
| `PEXELS_API_KEY` | https://www.pexels.com/api/ | Recommended |
| `PIXABAY_API_KEY` | https://pixabay.com/api/docs/ | Optional |

### 3. Initialize Database

```bash
npm run db:migrate
npm run seed
```

### 4. Run locally (development)

**Dashboard only** (browser):

```bash
npm run dev
# http://localhost:3000
```

**Desktop app** (Electron + dashboard + worker integration):

```bash
cd apps/desktop
npm install
npm run dev
```

### 5. Run Worker (Dry Run)

```bash
npm run worker:daily -- --dry-run
```

This tests the full pipeline without API calls or YouTube uploads.

### 6. Run Worker (Production)

```bash
# Full daily pipeline
npm run worker:daily

# Individual steps
npm run worker:render
npm run worker:upload
npm run worker:analytics
```

---

## CLI Commands

| Command | Description |
|---------|-------------|
| `npm run dev` | Start dashboard dev server |
| `npm run build` | Build dashboard for production |
| `npm run worker:dev` | Start worker in dev mode |
| `npm run worker:daily` | Run full daily content pipeline |
| `npm run worker:render` | Process the render queue |
| `npm run worker:upload` | Upload rendered videos to YouTube |
| `npm run worker:analytics` | Fetch YouTube analytics |
| `npm run db:migrate` | Run database migrations (creates/upgrades the default channel) |
| `npm run seed` | Seed database with defaults |
| `npm run worker:channel` | Manage channels (create/list/set-secret/connect/etc.) |
| `npm run test` | Run all tests |

Worker commands invoke Python internally but are exposed as npm scripts for consistency.

### Multi-channel flags

Most worker commands accept a channel target:

```bash
npm run worker:daily -- --channel <slug|id>   # one channel
npm run worker:daily -- --all-active          # every active channel, sequentially
npm run worker:full  -- --all-active          # generate + upload for all active channels
```

See [docs/multi-channel.md](docs/multi-channel.md) for the full channel CLI and the dashboard **Channels** page.

---

## Desktop application

The end goal is a **packaged local app** (Windows installer via Electron Builder), not a hosted deployment.

| Mode | Command |
|------|---------|
| Dev — desktop shell | `cd apps/desktop && npm run dev` |
| Dev — dashboard only | `npm run dev` |
| Build installer | `cd apps/desktop && npm run build:dashboard && npm run package` |

The desktop scheduler runs `worker:full --all-active` daily for every active channel. Multi-channel setup: [docs/multi-channel.md](docs/multi-channel.md). Full desktop guide: [docs/desktop-app.md](docs/desktop-app.md). Worker/FFmpeg details: [docs/worker-setup.md](docs/worker-setup.md).

---

## YouTube API Setup

1. Create a project in [Google Cloud Console](https://console.cloud.google.com)
2. Enable the YouTube Data API v3
3. Create OAuth 2.0 credentials
4. Complete the OAuth consent screen
5. Generate a refresh token

> **Audit Limitation**: New YouTube API projects are limited to private uploads until your app passes a Google audit. See [docs/youtube-oauth-setup.md](docs/youtube-oauth-setup.md) for full instructions.

---

## Content Pipeline

```mermaid
graph TD
    A[Daily Scheduler] --> B[Select Topic]
    B --> C[Generate 3-5 Short Stories]
    B --> D[Generate 2-5 Long-Form Stories]
    C --> E[Policy/Safety Check]
    D --> E
    E --> F[Generate TTS Audio]
    F --> G[Align Captions]
    G --> H[Download Background Footage]
    H --> I[Render Shorts 1080x1920]
    H --> J[Render Long-Form 1920x1080]
    I --> K[Generate SEO Metadata]
    J --> K
    K --> L[Upload to YouTube]
    L --> M[Track Analytics]
```

---

## Safety & Compliance

StoryFactory includes multiple safety layers:

- **Local keyword screening** - instant blocking of harmful content
- **AI-powered policy review** - nuanced content analysis
- **Automated rewriting** - fixes minor violations while preserving stories
- **Asset license verification** - only uses verified-free assets
- **Anti-spam guardrails** - prevents duplicate/low-quality content
- **YouTube compliance** - `madeForKids=false`, synthetic media disclosure, privacy fallback

---

## Testing

```bash
# All tests
npm run test

# Worker tests only
npm run test:worker

# Dashboard tests only
npm run test:dashboard
```

---

## Configuration

Configuration is layered: **app-level defaults** come from environment variables
(see [`.env.example`](.env.example)), and **per-channel overrides** (integrations,
API keys, quotas, prompts, niche) live in the database and are managed from the
dashboard **Channels** page or the `worker:channel` CLI. Anything a channel does
not override inherits the app-level default.

App-level keys:
- `STORYFACTORY_SECRET_KEY` – master key that encrypts per-channel secrets at rest (generate with `npm run worker:channel -- generate-key`)
- `YOUTUBE_CLIENT_ID` / `YOUTUBE_CLIENT_SECRET` – shared Google Cloud OAuth app (each channel connects its own account)
- `DRY_RUN=true` – Test pipeline without API calls
- `AUTO_MODE=true` – Enable fully automated mode
- `FAIL_SAFE_ON_POLICY_FLAG=true` – Block flagged content (default; overridable per channel)
- `UPLOAD_PRIVACY_MODE=public` – Default upload visibility (overridable per channel)
- `ALLOW_PRIVATE_FALLBACK=true` – Fall back to private if public fails

> **Security:** per-channel API keys and OAuth tokens are stored encrypted in the
> database and are never exposed to the client, written to `.env`, or logged.

---

## License

MIT
