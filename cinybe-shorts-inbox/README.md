# CinybeShorts inbox

**Drop the episode / movie files you want to recap into this folder.**

This is the local source folder for the **CinybeShorts** channel (`recap_shorts`
pipeline). The app only ever reads files you place here — it never downloads from
streaming services. You are responsible for sourcing the footage and for
copyright/fair-use compliance on uploads.

## How to name files

The pipeline reads the show/episode from the filename. Two layouts work:

### Flat (named by convention)

```
cinybe-shorts-inbox/
├── Breaking Bad S01E03.mkv      ← series, season 1, episode 3
├── The Office 2x05.mp4          ← series, season 2, episode 5
├── Inception (2010).mp4         ← movie
└── Dune Part 2.mkv              ← movie, part 2
```

### Per-series subfolders (subfolder name = the show)

```
cinybe-shorts-inbox/
└── Breaking Bad/
    ├── Breaking Bad S01E01.mkv
    └── Breaking Bad S01E02.mkv
```

Accepted extensions: `.mp4 .mkv .mov .webm .avi .m4v .ts`

## What happens

- New files are registered automatically on each run, or immediately with
  `npm run worker -- recap inbox scan --channel cinybe-shorts`.
- The app processes **one episode at a time**, cutting many Shorts from it
  (default ~52s each, up to 5 per run), and never re-cuts a span it already made
  a Short from. When an episode is fully covered it advances to the next file.
- Cut clips are written to `apps/worker/data/sources/clips/` (temporary).

See `docs/multi-channel.md` for the full segmentation rules and the `recap` CLI.

> Note: this folder lives inside the project, which is under OneDrive — large
> video files placed here will sync to OneDrive. If that's undesirable, point
> `recap.inbox_path` (in the channel config) at a folder outside OneDrive.
