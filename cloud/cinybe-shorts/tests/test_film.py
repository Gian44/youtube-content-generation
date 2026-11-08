from cny import align, cut, store

SRT = "﻿1\r\n00:00:05,000 --> 00:00:07,500\r\n<i>Hello.</i>\r\n\r\n2\r\n00:00:08,000 --> 00:00:09,000\r\n{\\an8}Who are you?\r\nI don't know.\r\n\r\n\r\n00:01:40,120 --> 00:01:42,000\r\nLater.\r\n"


def test_parse_srt_and_transcript_lines():
    cues = align.parse_srt(SRT)
    assert [(c.start, c.end, c.text) for c in cues] == [(5.0, 7.5, "Hello."), (8.0, 9.0, "Who are you? I don't know."), (100.12, 102.0, "Later.")]
    lines = align.transcript_lines(cues)
    assert lines == "[00:00] Hello. Who are you? I don't know.\n[01:40] Later."
    big = [align.Cue(i * 10.0, i * 10.0 + 2, "x" * 100) for i in range(800)]
    thin = align.transcript_lines(big, max_chars=30000)
    assert len(thin) < 45000 and thin.count("\n") == 799 and thin.startswith("[00:00] xxxx")


def test_pick_shots_spreads_across_scene_cuts():
    cuts = [110.0, 118.0, 121.0, 140.0, 160.0, 175.0]
    shots = cut.pick_shots(100.0, 200.0, cuts, want=3, min_len=3.0, max_len=8.0)
    assert len(shots) == 3 and shots[0][0] == 100.15 and all(1.0 <= d <= 8.0 for _, d in shots)
    assert shots[0][0] < shots[1][0] < shots[2][0]
    # a 2-second scene (118-121) is never picked
    assert all(not (118.0 <= s < 121.0) for s, _ in shots)
    # too few cuts → even split
    even = cut.pick_shots(100.0, 130.0, [], want=4, min_len=3.0, max_len=8.0)
    assert len(even) == 4 and even[1][0] - even[0][0] == 7.5 and even[0][1] == 7.2
    # fewer scenes than wanted → the longest scenes are split into several shots, still in screen order
    split = cut.pick_shots(100.0, 200.0, [130.0, 150.0], want=6, min_len=3.0, max_len=8.0)
    assert len(split) == 6 and [x[0] for x in split] == sorted(x[0] for x in split) and all(3.0 <= d <= 8.0 for _, d in split)
    assert len({x[0] for x in split}) == 6                                       # six different moments, no replays
    # a short window cannot be split below min_len: it returns what fits
    assert len(cut.pick_shots(100.0, 120.0, [108.0, 112.5], want=12, min_len=3.0, max_len=8.0)) <= 6


def test_cut_commands():
    sc = cut.scene_cmd("f.mp4", start=100.0, seconds=60.0, threshold=0.3)
    assert "select='gt(scene,0.3)',showinfo" in sc and sc[sc.index("-ss") + 1] == "100.000"
    sh = cut.shot_cmd("f.mp4", "o.mp4", start=12.5, seconds=6.0, width=1920, height=1080, fps=30)
    vf = sh[sh.index("-vf") + 1]
    assert sh[sh.index("-ss") + 1] == "12.500" and "-an" in sh and "boxblur" in vf and "overlay=(W-w)/2:(H-h)/2" in vf   # blurred fill
    pad = cut.shot_cmd("f.mp4", "o.mp4", start=1, seconds=2, width=1920, height=1080, fps=30, fit="pad")
    assert "pad=1920:1080" in pad[pad.index("-vf") + 1]
    fa = cut.film_audio_cmd("f.mkv", "a.m4a")
    assert fa[-1] == "a.m4a" and "-vn" in fa and fa[fa.index("-map") + 1] == "0:a:0"
    shv = cut.shot_cmd("f.mp4", "o.mp4", start=12.5, seconds=6.0, width=1080, height=1920, fps=30, fit="crop")
    assert "crop=1080:1920" in shv[shv.index("-vf") + 1] and "pad=" not in shv[shv.index("-vf") + 1]


def test_store_key_matching_and_config(monkeypatch):
    keys = ["films/Inception-2010.MKV", "films/inception-2010.en.srt", "memento-2000.mp4", "notes.txt"]
    f = store.match_keys("inception-2010", keys)
    assert f.video_key == "films/Inception-2010.MKV" and f.srt_key == "films/inception-2010.en.srt"
    assert store.match_keys("memento-2000", keys).srt_key is None
    assert store.match_keys("tenet-2020", keys).video_key is None
    assert store.match_keys("inception-2010", ["published--inception-2010.mkv"]).video_key is None   # done films never match
    from cny import config
    monkeypatch.setenv("R2_ACCOUNT_ID", "acc"); monkeypatch.setenv("R2_ACCESS_KEY_ID", "k"); monkeypatch.setenv("R2_SECRET_ACCESS_KEY", "s")
    cfg = config.Config.from_env(minutes=10)
    assert store.configured(cfg) and cfg.r2_bucket == "cinybe-films"
    monkeypatch.delenv("R2_SECRET_ACCESS_KEY")
    assert not store.configured(config.Config.from_env(minutes=10))
    # Drive wins over R2 when a service-account key is set; the folder is found by name unless an id is given
    monkeypatch.setenv("GDRIVE_SERVICE_ACCOUNT_JSON", '{"type": "service_account"}')
    cfg = config.Config.from_env(minutes=10)
    assert store.backend(cfg) == "drive" and store.label(cfg) == "drive:cinybe-films"
    monkeypatch.setenv("GDRIVE_FOLDER_ID", "1abc")
    assert store.label(config.Config.from_env(minutes=10)) == "drive:1abc"


def test_drive_trash_falls_back_to_rename():
    class Files:
        def __init__(self, calls):
            self.calls = calls

        def update(self, **kw):
            self.calls.append(kw["body"])
            if "trashed" in kw["body"]:
                class Boom:
                    def execute(self_):
                        raise RuntimeError("insufficientFilePermissions")
                return Boom()
            class Ok:
                def execute(self_):
                    return {}
            return Ok()

    class Svc:
        def __init__(self, calls):
            self._files = Files(calls)

        def files(self):
            return self._files

    calls = []
    dr = store.Drive.__new__(store.Drive)
    dr.svc, dr.ids = Svc(calls), {"films/inception-2010.mkv": "id1"}
    assert dr.trash("films/inception-2010.mkv") == "renamed"
    assert calls == [{"trashed": True}, {"name": "published--inception-2010.mkv"}]


def test_wav_samples_for_whisper(tmp_path):
    import subprocess
    wav = str(tmp_path / "a.wav")
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "sine=frequency=440:duration=0.5", "-ac", "1", "-ar", "16000",
                    "-c:a", "pcm_s16le", wav], check=True)
    a = align._wav_samples(wav)
    assert a.dtype.name == "float32" and a.shape == (8000,) and -1.0 <= float(a.min()) and float(a.max()) <= 1.0


def test_film_audio_extraction_and_real_bed_mix(tmp_path):
    """Real ffmpeg: a 40 s two-tone "film", shots mapped back to it, the bed cut and ducked under a voice."""
    import subprocess
    from cny import render
    from cc import segments as seg
    film = str(tmp_path / "film.mp4")
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "testsrc=size=320x240:rate=25:duration=40",
                    "-f", "lavfi", "-i", "sine=frequency=330:duration=40", "-shortest", "-c:v", "libx264", "-preset", "ultrafast",
                    "-c:a", "aac", film], check=True)
    fa = cut.extract_film_audio(film, str(tmp_path / "film_audio.m4a"))
    assert fa and cut.extract_film_audio(film, fa) == fa                       # resumable
    silent = str(tmp_path / "silent.mp4")
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "testsrc=size=320x240:rate=25:duration=3", "-c:v", "libx264",
                    "-preset", "ultrafast", silent], check=True)
    assert cut.extract_film_audio(silent, str(tmp_path / "none.m4a")) is None   # no audio stream → pad instead
    plan = [seg.Segment("clip", "p1.mp4", 2.0), seg.Segment("clip", "p2.mp4", 3.0, offset=1.0), seg.Segment("clip", "unknown.mp4", 1.0)]
    pieces = render.bed_pieces(plan, {"p1.mp4": 10.5, "p2.mp4": 30.5})
    assert pieces == [(10.5, 2.0), (31.5, 3.0), (-1.0, 1.0)]
    bed = str(tmp_path / "bed.wav")
    subprocess.run(render.bed_cmd(fa, pieces, 7.0, bed), check=True)
    assert abs(render.tts.duration_seconds(bed) - 7.0) < 0.05                   # padded to the section length
    voice = str(tmp_path / "voice.mp3")
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "sine=frequency=880:duration=5", "-ar", "24000", voice], check=True)
    mix = str(tmp_path / "mix.wav")
    subprocess.run(render.mix_film_cmd(voice, bed, mix), check=True)
    assert abs(render.tts.duration_seconds(mix) - 5.0) < 0.1                    # as long as the voice
    both = render.concat_audio([bed, bed], str(tmp_path / "both.wav"))
    assert abs(render.tts.duration_seconds(both) - 14.0) < 0.1


def _tone_db(path, f0, at, secs=1.5):
    import wave
    import numpy as np
    w = wave.open(path)
    sr, ch = w.getframerate(), w.getnchannels()
    x = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float32).reshape(-1, ch).mean(axis=1) / 32768
    seg = x[int(at * sr): int((at + secs) * sr)]
    spec = np.abs(np.fft.rfft(seg * np.hanning(len(seg)))) / (len(seg) / 4)
    k = int(round(f0 * len(seg) / sr))
    return 20 * np.log10(spec[k - 2:k + 3].max() + 1e-12)


def test_film_sound_ducks_under_the_voice_and_returns_in_the_gaps(tmp_path):
    """A 330 Hz "film" under a narration-loud 880 Hz "voice" (3 s on, 3 s pause, 3 s on): the film sound must drop
    clearly while the voice speaks and come back in the pause (measured 12.5 dB on 2026-11-08)."""
    import subprocess
    from cny import render
    bed, voice, mix = (str(tmp_path / n) for n in ("bed.wav", "voice.wav", "mix.wav"))
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "sine=frequency=330:duration=9", "-ac", "2", "-ar", "48000", bed],
                   check=True)
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "sine=frequency=880:duration=3", "-f", "lavfi", "-i",
                    "anullsrc=r=44100:cl=mono", "-f", "lavfi", "-i", "sine=frequency=880:duration=3", "-filter_complex",
                    "[1:a]atrim=duration=3[s];[0:a][s][2:a]concat=n=3:v=0:a=1,loudnorm=I=-18:TP=-2:LRA=11,aresample=24000", voice],
                   check=True)
    subprocess.run(render.mix_film_cmd(voice, bed, mix), check=True)
    under, gap = _tone_db(mix, 330, 1.0), _tone_db(mix, 330, 4.0)
    assert 8.0 <= gap - under <= 20.0, (under, gap)


def test_burned_in_bars_are_detected_and_cropped_before_the_fill(tmp_path):
    import subprocess
    # pure: largest area seen wins (dark scenes over-crop), centred; no real bars → None
    assert cut.union_crop([(1440, 1080, 240, 0), (1300, 1000, 300, 40), (1440, 1076, 240, 2)], 1920, 1080) == "1440:1080:240:0"
    assert cut.union_crop([(1920, 1080, 0, 0), (1900, 1070, 10, 4)], 1920, 1080) is None
    assert cut.union_crop([], 1920, 1080) is None
    assert cut.union_crop([(1920, 800, 0, 140)], 1920, 1080) == "1920:800:0:140"            # scope film, letterboxed
    vf = cut.shot_vf(width=1080, height=1920, fps=30, fit="crop", crop="1440:1080:240:0")
    assert vf.startswith("crop=1440:1080:240:0,scale=1080:1920")
    # real ffmpeg: a 4:3 picture burned into a 16:9 file with black pillars
    src = str(tmp_path / "boxed.mp4")
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "testsrc=size=480x360:rate=25:duration=12",
                    "-vf", "pad=640:360:80:0:black", "-c:v", "libx264", "-preset", "ultrafast", src], check=True)
    assert cut.detect_crop(src, 12.0, points=3) == "480:360:80:0"
