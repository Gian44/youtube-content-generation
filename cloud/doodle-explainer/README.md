# sylusnix: stick-figure explainers (re-launched 2026-11-26)

Original explainers answering questions everyone has wondered about. Every word written fresh (Claude on Gian's
subscription), every frame drawn for the video (SVG → PNG, the channel's own stick figures from `dx/figures.py`),
narrated by Kokoro `af_bella`. No footage of anyone else: the channel replaced the Twitch-clip lane, which YouTube's
2026-10-01 Shorts originality change held at zero views.

- `python run.py --kind short` — 2 Shorts (first public at once, second 8 h later)
- `python run.py --kind long` — one 8-10 min video every second day from 2026-11-26 (`--force` ignores the rule)
- `--skip-upload` renders only; `--smoke` uploads unlisted and leaves the ledger alone.

Workflow: `.github/workflows/sylusnix-explainers.yml`. Design notes: project doc `claude/sylusnix-niche-pivot.md`.
