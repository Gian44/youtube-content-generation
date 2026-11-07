# CinybeShorts — cloud pipeline (narrated movie recaps)

Unattended narrated recaps of one film every 2nd day, rendered on GitHub Actions and uploaded to the
CinybeShorts YouTube channel: a ~10-minute long recap plus 3 Shorts per film. No desktop app, no database,
no PC. Visuals come from **the film itself** when Gian has put its file in the private R2 bucket (shots cut
from the scenes each act describes), otherwise from stock b-roll; act title cards in both cases. Wikipedia's
plot is the only text source.

```
films.yml (themes in order; the first film without a ledger row)
  → metadata: Wikidata (director, cast, genres, the Wikipedia title); TMDB first when TMDB_API_KEY is set
  → plot: the article's Plot/Synopsis section (≥ 250 words) or plots/<slug>.md written by the operator;
    thinner → `kind: skipped` ledger row, next film
  → plan (cny/planner.py, ported from the StoryFactory narrated-recap branch): acts (9–13 for 10 min) with
    b-roll searches → long-form sections + 3 Shorts, validated, retried once with the errors, then the
    no-AI-slop pass (sof/noslop.py, mode "recap") on flagged paragraphs only
  → narration: the Sleep On Facts voice stack (sof/tts.py) — voice F (Pocket TTS, free, on the runner) by
    default; TTS_BACKEND=gemini + GEMINI_API_KEY_CINYBE switches to Charon on this channel's own free tier
  → the film (cny/store.py, cny/align.py, cny/cut.py): `<slug>.mp4` + `<slug>.srt` fetched from the R2 bucket;
    the .srt becomes a "[mm:ss] dialogue" transcript and one writer call picks a 45–180 s window per act
    (validated: in order, inside the runtime; faster-whisper on the CPU when there is no .srt; proportional
    windows as the last resort); ffmpeg scene detection inside each window → 5 shots of 3–8 s per act, cut
    silent and normalised; the file is deleted after the Shorts are rendered
  → otherwise b-roll per act: Pexels/Pixabay video search on the act's queries, preview frames checked by
    Codex/Claude ("does this fit the moment?"), 3 clips per act; stock photos when nothing fits; neighbours'
    clips when the photos fail too
  → long recap: act card (2.5 s) + the act's clips rotating under that section's narration, crossfaded,
    cut to the exact section length; faint ambient bed; chapters at act starts; thumbnail = a frame of the
    film's own b-roll + title + hook
  → Shorts: 1080×1920 crops of the act's clips, burned captions, title band; voiced with the same voice,
    −16 LUFS
  → upload (Film & Animation): long video public at once; Short 1 an hour later, Shorts 2–3 one and two
    days later; ledger.json committed
```

Everything that is not recap-specific is imported from `../sleep-on-facts/sof` (see `cny/__init__.py`):
writer (`llm`), voice (`tts`), footage and photos, vision checks, no-slop, uploader, ledger. One fix there
lands here; this package never modifies `sof`.

## Running

```bash
gh workflow run cinybe-daily.yml -f minutes=3 -f skip_upload=true      # tests + render only (~20 min)
gh workflow run cinybe-daily.yml -f minutes=3                          # smoke: unlisted upload, ledger untouched
gh workflow run cinybe-daily.yml -f minutes=10 -f film=Inception -f year=2010   # a chosen film, public
gh workflow run cinybe-daily.yml                                        # the next film in films.yml
```

Locally: `python run.py --minutes 3 --skip-upload --film Memento --year 2000` (needs ffmpeg, the Python deps
and the env below; Pocket TTS needs `HF_TOKEN`).

## Secrets (repo → Settings → Secrets → Actions)

Shared with the sleep channel: `CLAUDE_CODE_OAUTH_TOKEN`, `CODEX_AUTH_JSON`, `HF_TOKEN`, `GEMINI_API_KEY`
(script fallback), `PEXELS_API_KEY`, `PIXABAY_API_KEY`, `YT_CLIENT_ID`, `YT_CLIENT_SECRET`, `OPENAI_API_KEY`
(optional). This channel's own: `YT_CLIENT_ID_CINYBE`, `YT_CLIENT_SECRET_CINYBE`, `YT_REFRESH_TOKEN_CINYBE` (each StoryFactory
channel was authorised with its own Google OAuth client; re-authorise with the StoryFactory CLI
`channel connect-youtube --channel cinybe-shorts` on the PC, then `_cowork_tmp/two-channels/set_channel_secrets.py`
copies them), optional `GEMINI_API_KEY_CINYBE` (a key from a Google Cloud project of its own, for the Charon
voice) and `TMDB_API_KEY`. The film store: `R2_ACCOUNT_ID`, `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY` (an R2 API
token with Object Read & Write on the bucket; `R2_BUCKET` is a workflow env, default `cinybe-films`).

## The film files

Upload with rclone (the same R2 credentials, S3 endpoint `https://<account id>.r2.cloudflarestorage.com`) as
`<slug>.mp4|.mkv|.mov` plus `<slug>.srt` (or `<slug>.en.srt`), slug as in `films.yml`'s comment, e.g.
`inception-2010.mkv` + `inception-2010.srt`. A folder prefix is fine. The run downloads the file for the film
it is about to make (2–4 GB, a minute or two), cuts the shots and deletes it from the runner. Descriptions
carry the commentary/criticism line instead of the stock-footage line. Films without a file in the bucket go
out with stock b-roll, so the queue never waits on an upload.

## Queue and ledger

`films.yml` is the queue (comments inside). `ledger.json` rows: `kind: video` (published: film, year, theme,
video_id, title, shorts, plot source, voice, run) and `kind: skipped` (film, reason). The daily cron does
nothing while the last video is younger than 36 h (`gap_hours`); a forced `film` input always runs. The
operator keeps `films.yml` and `plots/` fed by commit; nothing else needs a human.

## Files

| file | purpose |
|---|---|
| `cny/config.py` | tunables; wraps `sof.config.Config` for keys and voice |
| `cny/films.py` | films.yml, next film, the 36-hour gap |
| `cny/meta.py` | Wikidata / TMDB metadata |
| `cny/plot.py` | Wikipedia plot + lead; operator synopses |
| `cny/planner.py` | acts → scripts, validators, no-slop pass |
| `cny/store.py` | the R2 bucket: key matching, download |
| `cny/align.py` | .srt parsing, act → time windows (writer call), whisper fallback, proportional fallback |
| `cny/cut.py` | scene detection and shot extraction |
| `cny/visuals.py` | film shots per act, or b-roll per act with the vision check; photo fallback |
| `cny/render.py` | segment planning, act cards, long recap and Shorts composition |
| `cny/thumbnail.py` | the thumbnail |
| `cny/seo.py` | titles, descriptions, tags, Short publish times |
| `cny/pipeline.py` | orchestration, resumable `work/<date>-<slug>/`, ledger |
