# CinybeShorts — cloud pipeline (narrated movie recaps)

Unattended narrated recaps in the recap-channel style, rendered on GitHub Actions and uploaded to the
CinybeShorts YouTube channel: one ~17-minute recap (told fast) plus 3 Shorts per film, **one film a day**. No desktop
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
  → plan (cny/planner.py): call 1 reads the transcript → 13–19 acts for 17 min, each with its own time range
    in the film (validated: in order, inside the runtime, ≥ 20 s) + the main people; call 2 gets the acts AND
    each act's subtitle lines with their own timestamps, and writes the narration as **beats** — 1–3 short
    sentences about ONE moment, each with `at`, the film time of that moment (taken from the line it is about)
    — the way the sample channels tell it: present tense, scene by scene, fast, names only (Gian: "Just say the
    name"), a short line quoted word for word now and then; a premise-and-twist title (60–100 chars, no film or
    person's name), thumbnail text, description, tags, 3 Shorts (also as timed beats). Beat times are repaired,
    not rejected (unreadable → interpolated, backwards → lifted, outside the act → clamped); a beat that quotes
    a line is snapped to that line's exact subtitle time (cny/beats.py). No-AI-slop pass per beat, so every beat
    keeps its moment
  → film check (cny/visuals.py, cny/cut.py), before any narration is spent: the file must decode; bars burned
    into it are detected (cropdetect) and cropped; ffmpeg scene detection over each act (± 30 s, on a 480-px
    copy) — cached in cuts.json
  → narration: the Sleep On Facts voice stack (sof/tts.py) — voice F (Pocket TTS, free, on the runner), **one take
    per beat** so each beat's spoken length is known to the frame; each take has its pauses capped at ~0.24 s
    (alone +30 %) and is played ×1.15 (atempo) → ~242 wpm (Gian: "make it faster to retain audience")
  → long recap, beat by beat (cny/beats.py, cny/render.py) — Gian 2026-11-08: "the video is showing something
    and the narration is explaining something different": under every beat, the film from the moment it tells
    (0.5 s before), cut in screen order (~3-s shots, a sliver of the previous scene before a cut is never used,
    long takes split into moments, nothing replayed), exactly as long as the beat is spoken; one ffmpeg call per
    beat reading the pieces straight from the film (crop → 16:9 blurred fill), beats concatenated; per-beat log
    in beats.json (moment, window, pieces). Footage muted, the voice over the quiet ambient bed (`film_audio=True`
    lays the film's own sound, cut with the same pieces, under the voice instead, sidechain-ducked ~12 dB);
    chapters at act starts; thumbnail = the film's own frame at the first Short's moment + the thumbnail text
    in gold caps + a "MOVIE RECAP" tag, no film name
  → Shorts: the same, 9:16 (≤ 3.5-s shots), captions timed per beat, hook as title band; −16 LUFS voice
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
**Daily cadence** (Gian, 2026-11-12; `cny/daily.py`): one recap per UTC day. GitHub starts scheduled runs hours
late (6–10 h seen) and sometimes drops one, so the workflow has four triggers (09:41, 13:41, 17:41, 21:41 UTC) and
a `gate` job that reads the latest `ledger.json` with the standard library alone (`gate.py`, seconds, before any
install): before 09:00 UTC, or once a recap made today is up, the trigger ends there; the first one after 09:00
on a day without a recap renders. A recap counts for the day its run started (`date`), so one that starts at 23:00
and uploads after midnight does not take the next day's slot. Under that, `gap_hours` (6) keeps two recaps from
landing within hours of each other. Manual runs skip the hour rule, not the one-a-day rule; a forced `film` always
runs. The writer gets three tries per call (`planner.ATTEMPTS`), and a Short up to twice its word cap loses its
last beats instead of failing the plan, so one bad draft does not cost the day. With 3 Shorts per film (at +1 h,
+1 d, +2 d), a daily recap means ~3 Shorts a day; that is ~6,400 of the channel project's 10,000 YouTube units.
The public-domain queue holds ~6 weeks of films; a file Gian drops in Drive for an earlier queued film goes first. The operator keeps `films.yml` and the Drive folder fed; `plots/<slug>.md` is an optional
hand-written reference synopsis.

## Files

| file | purpose |
|---|---|
| `cny/config.py` | tunables; wraps `sof.config.Config` for keys and voice |
| `cny/films.py` | films.yml, next film, the minimum gap between recaps |
| `cny/daily.py` | one recap per UTC day: the trigger rule (standard library only) |
| `gate.py` | the workflow's gate job: does this trigger make today's recap? |
| `cny/meta.py` | Wikidata / TMDB metadata |
| `cny/plot.py` | Wikipedia plot (reference only); operator synopses |
| `cny/store.py` | the film store: Google Drive (service account) or R2; key matching, download, trash; archive dispatch |
| `cny/archive.py` | Internet Archive items: file list, best video, resumable download, the item's own .srt |
| `cny/subs.py` | OpenSubtitles: file hash, search, ranking, download |
| `cny/align.py` | .srt parsing → timed transcript; whisper fallback |
| `cny/planner.py` | transcript → acts with time ranges + people; scripts; validators; no-slop pass |
| `cny/cut.py` | scene detection, shot picking in a window, burned-in bars, the 16:9 fill / 9:16 crop filter |
| `cny/visuals.py` | scene cuts per act (± 30 s), cached |
| `cny/beats.py` | beat times (parse, repair, snap quoted lines), each beat's film window and pieces, one ffmpeg render per beat |
| `cny/render.py` | long recap and Shorts from the beats, captions per beat, film-sound mix, timing helpers |
| `cny/thumbnail.py` | the thumbnail: film frame + hook, no film name |
| `cny/seo.py` | titles (hook, never the film name), descriptions, tags, Short publish times |
| `cny/pipeline.py` | orchestration, resumable `work/<date>-<slug>/`, ledger |
