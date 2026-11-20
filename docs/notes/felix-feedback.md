# Felix's feedback: lessons (2026-11)

Felix (Puello Media, a client editor) said our programmatic videos look "made by Claude": too many stills, too much
on-screen text, stock-sounding effects. His rules, as applied per channel, are in each channel README under "Visual
style rules (Felix, 2026-11)". One line per lesson; update a line instead of adding a near-copy; delete what turns out wrong.

## Footage
- How much footage shows depends on where clips may go, not how many are found: 30 clips filled 1 of 394 Sleep On Facts slots because only slots with nothing named were open; a named thing's own clips belong in its own slots.
- Vision-check the part of a clip that will be shown (frames of the chosen window), never just its poster frame.
- Divide motion by picture contrast in a calm check, or dark space footage always reads as calm.
- Analyse a low-res rendition for cuts and flashes, then cut only the chosen window from the full-res URL; no 200 MB downloads.
- upload.wikimedia.org answers 429 after ~10 quick requests: pace every Commons request (an ffmpeg window read is several requests: webm ~4, ogv ~10) about 2 s apart across threads.
- NASA `~orig` film transfers and DVD-sourced Archive prints are often anamorphic: force square pixels before any cover-crop or they come out 12-16 % too wide or squeezed.
- Bound every step of a budgeted stage (search, vision, cut) with one total timeout, and do first the work that fills the most slots.
- Check footage code against live NASA and Commons from Gian's PC; the mocks hid the rate limit. The cloud session cannot reach either.

## On-screen text
- Show the key number with its unit ("$14", "7 survivors"), else a first-mentioned name, 1-3 words, a few a minute (`cc.textbudget`); never the sentence.
- A capitalised word mid-sentence is not a name: quoted dialogue ("They're", "Don't") and nationalities or creeds ("German", "Jew") must never be picked; use the plan's own name list when there is one.
- Never show what is already on screen, compared word by word with possessives stripped ("Kai's" repeats "Kai Cenat's"), numbers by their digits.
- When text is timed by character share, change capitals only, never characters, or the words drift off the voice.
- When a length cap on captions goes away, make sure long lines wrap instead of running off the frame.
- Enforce word limits in code after the model answers; when a model title is too long use a fixed fallback rather than cutting its words.
- Ask the writer for on-screen words and an icon alongside each spoken line, validate them, fall back to key words; never reject a script over display-only fields, and turn malformed JSON into the retryable writer error.
- Credits: description first, on screen only for overflow, sized against the rest of the description (YouTube's 5,000-character limit).

## Sound
- Set effect levels from what is actually heard: measure after the final loudnorm (it lifts a quiet countdown 6-10 dB) and against the clip's own measured peak, after any mono-to-stereo step (3 dB).
- Measure short sounds by decoding from the start (`atrim`), never by `-ss` seeking into MP3/AAC, which misses the attack by ~6 dB.
- Effects on a few cuts only (the payoff always), never two in a row; a soft synthesised woodblock/pluck/thump varied per video, no whoosh, static or sine beep.

## Framing
- No flat bands: a blurred, darkened copy of the clip fills what the sharp picture does not; blur at quarter size and scale up, it is cheap.
- For vertical Shorts from photos, cover-crop and pan slowly on a 2x canvas in 4:4:4 (half-pixel steps); zoompan cannot pan across a canvas of another shape.
- Before joining parts with `-c copy`, confirm every kind of part (with an effect, without, without audio) has identical stream settings.

## Icons
- On an all-ages channel an emoji block list needs creeds, flags, political signs, graves, romance and monsters, not just weapons and drink.
- In a guessing format an icon can give the answer away as surely as a word; check icons against the answer.
- Render every panel kind with the longest realistic label and look at it; a fit-to-box helper must shrink, never drop words.
