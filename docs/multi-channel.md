# Multi-Channel Guide

StoryFactory is a **local desktop application** ([desktop-app.md](desktop-app.md)); channels and integrations are stored in the on-machine database, not on a hosted service.

StoryFactory runs an arbitrary number of **channels**. Each channel is an
independent content operation — its own niche, content rules, prompt/voice
customization, selected integrations, and YouTube account — all sharing the same
pipeline (story → TTS → captions → assets → render → upload → analytics).

## Concepts

- **Channel** — the primary unit of configuration and operation. Has an
  identity (name, slug, status), a niche/content style, a per-channel `config`
  (quotas, category weights, TTS ratios, privacy, disclosure, caption style…),
  and a set of enabled **integrations**.
- **Integration** — a provider a channel uses: `text.openai`, `text.gemini`,
  `tts.openai`, `tts.gemini`, `assets.pexels`, `assets.pixabay`, `youtube`,
  `storage.local`, `storage.r2`. Each channel enables only what it needs and
  supplies its own credentials.
- **Inheritance** — anything a channel does not override falls back to the
  app-level defaults from `.env`. Prompts are global by default; a channel may
  override any prompt by name.

## App-level vs per-channel

| App-level (`.env`) | Per-channel (database) |
|---|---|
| `DATABASE_URL` / `SQLITE_PATH`, storage infra | Which integrations are enabled |
| `STORYFACTORY_SECRET_KEY` (master key) | API keys for each provider (encrypted) |
| Shared Google Cloud OAuth `YOUTUBE_CLIENT_ID` / `_SECRET` | YouTube refresh token per channel |
| Default values for quotas/ratios/privacy/etc. | Overrides for quotas, category weights, prompts, niche |

## Secrets & encryption

Per-channel API keys and OAuth tokens are stored **encrypted** in the database
using a Fernet master key. Resolution order:

1. `STORYFACTORY_SECRET_KEY` environment variable (recommended).
2. A key file at `STORYFACTORY_KEY_FILE` (default `./.storyfactory.key`),
   auto-generated on first use if neither is set.

Generate a key explicitly:

```bash
npm run worker:channel -- generate-key
# put the value in STORYFACTORY_SECRET_KEY (.env)
```

The key is never stored in the database and never committed (`.storyfactory.key`
is git-ignored). All encryption/decryption happens in the Python worker — the
dashboard never handles plaintext secrets.

## Migrating an existing single-channel install

Run the migration. It is idempotent and backfills a **default channel** from
your current `.env`, then scopes all existing batches/stories/renders/uploads to
it — no re-setup required:

```bash
npm run db:migrate
```

This creates a channel (slug `default`, name from `CHANNEL_NAME`), imports the
keys present in `.env` as that channel's integrations (encrypted), and adds
`channel_id` to all operational tables.

## Managing channels

From the dashboard **Channels** page you can:

- Create / duplicate / pause / delete channels
- Set each channel's niche and content config
- Enable integrations and enter per-channel API keys (stored encrypted)
- **Connect a YouTube account per channel** (the OAuth `state` carries the
  channel id; the token is stored encrypted for that channel only)

The same operations are available from the CLI:

```bash
# Create a channel and import keys from the current .env
npm run worker:channel -- create --name "Horror Nights" --slug horror --niche "scary stories" --import-env

# List channels
npm run worker:channel -- list

# Set a per-channel API key (encrypted)
npm run worker:channel -- set-secret --channel horror --provider text.openai --secret api_key=sk-... --enable

# Per-channel prompt override (falls back to the global prompt otherwise)
npm run worker:channel -- set-prompt --channel horror --name short_story_generation --template-file ./horror_prompt.txt

# Show per-channel integration status (no secret values)
npm run worker:channel -- status --channel horror

# Pause / activate / duplicate / delete
npm run worker:channel -- set-status --channel horror --status paused
npm run worker:channel -- duplicate --channel horror --name "Horror Nights 2"
npm run worker:channel -- delete --channel horror   # only if it has no history
```

### Connecting YouTube per channel

1. Set the shared Google Cloud OAuth app once in `.env`
   (`YOUTUBE_CLIENT_ID`, `YOUTUBE_CLIENT_SECRET`, `YOUTUBE_REDIRECT_URI`).
2. On the **Channels** page, click **Connect YouTube** for the channel.
3. Approve access for the YouTube account you want that channel to publish to.

The refresh token is captured and stored encrypted for that channel. Uploads and
analytics for the channel use its own token. (Advanced: a channel may override
`client_id`/`client_secret` to use a separate Google Cloud project.)

## Running the pipeline per channel

```bash
# A single channel (default channel if --channel is omitted)
npm run worker:daily -- --channel horror
npm run worker:upload -- --channel horror
npm run worker:analytics -- --channel horror

# Every active channel, sequentially (fail-isolated, quota-aware)
npm run worker:daily -- --all-active
npm run worker:full -- --all-active
```

The dashboard's run buttons target the **active channel** (selected in the
sidebar switcher). The desktop scheduler runs `full --all-active` daily.

### Quota note

Because all channels share one Google Cloud project, they share the YouTube API
daily quota. Runs are sequential and quota-aware; a single `videos.insert` costs
~1600 units of the 10,000/day default. Stagger or cap uploads per channel if you
operate many channels.
