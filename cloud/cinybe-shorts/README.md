# CinybeShorts — cloud pipeline (narrated movie recaps)

Unattended narrated recaps in the recap-channel style, rendered on GitHub Actions and uploaded to the
CinybeShorts YouTube channel: one ~17-minute recap (told fast) plus 3 Shorts per film, one film every 2nd day. No desktop
app, no database, no PC. **Everything on screen is the film itself** — shots cut, in screen order, from the
scenes each act covers — and the recap is written from the film's own dialogue (its subtitles). There is no
stock fallback: a queued film whose file is not in the store is passed over, and nothing is posted
(Gian, 2026-11-08: "If it is not from the movie itself, do not post anything").

```
films.yml (themes in order; the first film without a ledger row that the run can have)
  → the film (cny/store.py): Gian's `<slug>.mkv|mp4` + `<slug>.srt` from the Google Drive folder shared with the
    service account (or an R2 bucket) — or, for a film marked `archive: <identifier>`, a public-domain print the
    run downloads itself from the Internet Archive (cny/archive.py: the item's largest proper video under 5 GB,
    resume on drops). Deleted from the runner after the shots are cut; Gian's file trashed in Drive once published
  → transcript (cny/align.py): the .srt as "[mm:ss] dialogue" lines (one per 10 s), trimmed to ~90k chars. Timed
    to this very file first, because the act ranges become shot ranges: the file's own human .srt → an
    OpenSubtitles hash match (cny/subs.py) → the Archive's ASR .srt made from that file → faster-whisper on the
    runner's CPU → an OpenSubtitles title match only when all of that came up short; fewer than 120 cues →
    nothing posted (an archive film then gets a `kind: skipped` ledger row so the queue moves on)
  → reference (cny/meta.py, cny/plot.py): Wikidata/TMDB metadata; Wikipedia's Plot section as an optional
    reference the writer may use — never the source of the script; unavailable → the run goes on
  → plan (cny/planner.py): call 1 reads the transcript → 16–22 acts for 20 min, each with its own time range
    in the film (validated: in order, inside the runtime, ≥ 20 s) + the main people (name + who they are);
    call 2 gets the acts AND each act's own dialogue lines, and writes the narration the way the sample
    channels do (measured from their captions, 2026-11-08): present tense, scene by scene, fast (short
    sentences, every line moves the story, each act ends on a pull into the next), people called by name only
    ("Ben", never "a drifter named Ben" — Gian: "Just say the name"), a short line quoted word for word now
    and then; a premise-and-twist title of 60–100 chars in Title Case with no film name, no person's name and
    no suffix ("CIA Built the Most Sadistic Kill Team … But It Quickly Spirals Out of Control"); thumbnail
    text, description (names the film), tags, 3 Shorts; validated, retried once with the errors; then the
    no-AI-slop pass (sof/noslop.py, "recap") on flagged paragraphs only
  → shots (cny/visuals.py, cny/cut.py), before any narration is spent: ffmpeg scene detection inside each
    act's time range → enough 2–5 s shots to carry that act's narration (one per 3 s + 2; when an act
    has fewer scenes than that, its longest scenes are split into several moments instead of replaying shots);
    4:3 and scope prints fill 16:9 with a blurred, darkened copy of the frame (no black bars); silent; the
    Shorts' acts also centre-cropped to 9:16; an act with no shots borrows its neighbours', a film that yields
    none fails the run
  → narration: the Sleep On Facts voice stack (sof/tts.py) — voice F (Pocket TTS, free, on the runner) by
    default; each chunk has its pauses capped at ~0.24 s and is played ×1.4 (atempo) → ~240 words per minute,
    quicker than the fastest sample (226) — Gian: "make it faster to retain audience"; TTS_BACKEND=gemini +
    GEMINI_API_KEY_CINYBE switches to Charon on this channel's own free tier
  → long recap (cny/render.py, cc/segments.py): per act, its shots played once each, in order, hard-cut, sized
    to the narration's exact length; no title cards; the footage is muted and the voice sits over the quiet
    ambient bed (Gian: "it's okay if it's mute"; of the 6 auto-captioned samples, 4 run a music bed, 2 let the
    film's sound through at moments, 1 is voice only). `film_audio=True` lays the film's own sound under the voice
    instead (cut to the same moments, −24 LUFS, sidechain-ducked ~12 dB); chapters at act starts; thumbnail = the film's own frame + the thumbnail text in gold
    caps + a "MOVIE RECAP" tag, no film name
  → Shorts: the act's 9:16 shots (≤ 3.5 s each) under the Short's narration, burned captions, hook as title
    band; −16 LUFS voice
  → upload (Film & Animation): long video public at once; Short 1 an hour later, Shorts 2–3 one and two
    days later; ledger.json committed; the film trashed in the store
```

Everything that is not recap-specific is imported from `../sleep-on-facts/sof` (see `cny/__init__.py`):
writer (`llm`), voice (`tts`), no-slop, uploader, ledger, music; segment planning from `../common/cc`. One fix
there lands here; this package never modifies `sof`.

## Running

```bash
gh workflow run cinybe-daily.yml -f minutes=3 -f skip_upload=true      # tests + render only
gh workflow run cinybe-daily.yml -f minutes=3                          # smoke: unlisted upload, ledger untouched
gh workflow run cinybe-daily.yml -f minutes=17 -f film=Inception -f year=2010   # a chosen film, public
gh workflow run cinybe-daily.yml                                        # the next queued film with a file
```

Locally: `python run.py --minutes 3 --skip-upload --film Memento --year 2000` (needs ffmpeg, the Python deps
and the env below; Pocket TTS needs `HF_TOKEN`).

## Secrets (repo → Settings → Secrets → Actions)

Shared with the sleep channel: `CLAUDE_CODE_OAUTH_TOKEN`, `HF_TOKEN`, `GEMINI_API_KEY` (script fallback),
`OPENAI_API_KEY` (optional). This channel's own: `YT_CLIENT_ID_CINYBE`, `YT_CLIENT_SECRET_CINYBE`,
`YT_REFRESH_TOKEN_CINYBE` (each StoryFactory channel was authorised with its own Google OAuth client;
re-authorise with the StoryFactory CLI `channel connect-youtube --channel cinybe-shorts` on the PC, then
`_cowork_tmp/two-channels/set_channel_secrets.py` copies them), optional `GEMINI_API_KEY_CINYBE` (a key from a
Google Cloud project of its own, for the Charon voice) and `TMDB_API_KEY`.

The film store, first choice **Google Drive** (free, no card): `GDRIVE_SERVICE_ACCOUNT_JSON` = the content of a
service-account key (Google Cloud → IAM → Service accounts → Keys; Drive API enabled on the project) and
`GDRIVE_FOLDER_ID` (optional: the folder is otherwise found by its name, `GDRIVE_FOLDER` = `cinybe-films`).
The folder lives in the channel's own Google Drive and is shared with the service account's e-mail as Editor.
Alternative, **Cloudflare R2**: `R2_ACCOUNT_ID`, `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY` (`R2_BUCKET` is a
workflow env, default `cinybe-films`).

Subtitles when a file has none: `OPENSUBTITLES_API_KEY` (opensubtitles.com → API consumers → new consumer),
`OPENSUBTITLES_USERNAME`, `OPENSUBTITLES_PASSWORD` (downloads need a login; the free tier allows a few a day, a
film needs one). Without them the runner transcribes with faster-whisper (slower, ASR quality).

## The film files

Drop two files into the Drive folder `cinybe-films`: `<slug>.mkv` (or `.mp4`, `.mov`, `.m4v`, `.webm`) plus
`<slug>.srt` (or `<slug>.en.srt`), slug as in `films.yml`'s comment, e.g. `inception-2010.mkv` +
`inception-2010.srt`. The next run downloads the file for the film it is about to make (2–4 GB, a minute or
two), cuts the shots, deletes it from the runner, and — once the recap is uploaded — moves it to the Drive's
trash (or, when Drive lets only the owner trash it, renames it `published--<name>` so it never matches again;
delete those by hand). Trashed files still count toward the 15 GB until the trash is emptied. A film in
`films.yml` without a file is simply passed over until its file appears; a film whose `.srt`
is missing is transcribed on the CPU (slower, ~1 min per 10 min of film). Descriptions name the film and
carry the commentary/criticism line.

## Public-domain films (no file from Gian)

The theme "Public domain classics" at the end of `films.yml` (43 US-public-domain talkies, curated 2026-11-08)
carries an `archive:` identifier per film; the run fetches that print itself, so the channel keeps posting
whenever the Drive folder is empty. Queue order rules: a Drive film earlier in the file is passed over while it
has no file, and the first archive film runs. Descriptions say the film is in the public domain and link the
Archive item. Caveats: prints are 480p–1080p and often 4:3 (pillarboxed in the 16:9 recap); the Archive is slow
(a 4 GB download takes ~10 min); public-domain status is per film in the US and distributors do file bogus
Content ID claims on them (dispute with the Archive link). Finding items: `_cowork_tmp/two-channels/ia_find2.py`
(archive.org advanced search → best item per title).

## Queue and ledger

`films.yml` is the queue (comments inside). `ledger.json` rows: `kind: video` (published: film, year, theme,
video_id, title, shorts, transcript source and cue count, plot reference used, shots, film file and source, voice,
run) and `kind: skipped` (an archive film the run gave up on, with the reason; delete the row to retry).
The daily cron does nothing while the last video is younger than 36 h (`gap_hours`); a forced `film` input
always runs. The operator keeps `films.yml` and the Drive folder fed; `plots/<slug>.md` is an optional
hand-written reference synopsis.

## Files

| file | purpose |
|---|---|
| `cny/config.py` | tunables; wraps `sof.config.Config` for keys and voice |
| `cny/films.py` | films.yml, next film, the 36-hour gap |
| `cny/meta.py` | Wikidata / TMDB metadata |
| `cny/plot.py` | Wikipedia plot (reference only); operator synopses |
| `cny/store.py` | the film store: Google Drive (service account) or R2; key matching, download, trash; archive dispatch |
| `cny/archive.py` | Internet Archive items: file list, best video, resumable download, the item's own .srt |
| `cny/subs.py` | OpenSubtitles: file hash, search, ranking, download |
| `cny/align.py` | .srt parsing → timed transcript; whisper fallback |
| `cny/planner.py` | transcript → acts with time ranges + people; scripts; validators; no-slop pass |
| `cny/cut.py` | scene detection and shot extraction (letterbox / centre-crop) |
| `cny/visuals.py` | shots per act from the act's own time range; neighbours' shots when an act has none |
| `cny/render.py` | long recap and Shorts composition (sequence planning in `cc/segments.py`) |
| `cny/thumbnail.py` | the thumbnail: film frame + hook, no film name |
| `cny/seo.py` | titles (hook, never the film name), descriptions, tags, Short publish times |
| `cny/pipeline.py` | orchestration, resumable `work/<date>-<slug>/`, ledger |
