import json
import subprocess

import pytest

from sof import marathon

RUN = "https://github.com/Gian44/youtube-content-generation/actions/runs/{}"


def _rows():
    return [
        {"date": "2026-10-20", "topic": "Saturn", "category": "space & astronomy", "video_id": "s1", "run_url": RUN.format(1)},
        {"date": "2026-10-21", "topic": "The Moon", "category": "space & astronomy", "video_id": "s2", "run_url": RUN.format(2)},
        {"date": "2026-10-22", "topic": "Pompeii", "category": "history & ancient civilisations", "video_id": "h1", "run_url": RUN.format(3)},
        {"date": "2026-10-23", "topic": "Jupiter", "category": "space & astronomy", "video_id": "s3", "run_url": RUN.format(4)},
        {"date": "2026-10-24", "topic": "Old", "category": "space & astronomy", "video_id": "x", "run_url": RUN.format(5), "abandoned": True},
    ]


def test_candidates_need_media_and_skip_used_and_abandoned():
    rows = _rows() + [{"kind": "marathon", "segments": ["h1"], "video_id": "m1", "date": "2026-10-25"}]
    c = marathon.candidates(rows, {"1", "2", "3", "4", "5"})
    assert [r["video_id"] for r in c] == ["s3", "s2", "s1"]                  # newest first, h1 used, x abandoned
    assert [r["video_id"] for r in marathon.candidates(_rows(), {"1", "4"})] == ["s3", "s1"]


def test_choose_group_prefers_a_full_category_then_mix():
    c = marathon.candidates(_rows(), {"1", "2", "3", "4"})
    cat, segs = marathon.choose_group(c)
    assert cat == "space & astronomy" and [s["topic"] for s in segs] == ["Saturn", "The Moon", "Jupiter"]
    cat, segs = marathon.choose_group(marathon.candidates(_rows(), {"2", "3", "4"}))
    assert cat == marathon.MIX and [s["topic"] for s in segs] == ["The Moon", "Pompeii", "Jupiter"]
    assert marathon.choose_group(marathon.candidates(_rows(), {"2"}))[1] == []
    assert marathon.choose_group(c, group="history & ancient civilisations")[1] == []


def test_title_description_and_chapters():
    segs = [r for r in _rows() if r["video_id"] in ("s1", "s2", "s3")]
    assert marathon.build_title("space & astronomy", segs, 9) == \
        "9 Hours of Space Facts to Fall Asleep To | Saturn, The Moon & Jupiter"
    assert marathon.build_title(marathon.MIX, segs, 9).startswith("9 Hours of Random Facts to Fall Asleep To |")
    ch = marathon.offset_chapters([("Saturn", 100.0, [{"start": 0, "title": "1. The Rings"}, {"start": 50, "title": "Titan"}]),
                                   ("The Moon", 120.0, [{"start": 0, "title": "How the Moon Formed"}, {"start": 60, "title": "Tides"}])])
    assert ch == [{"start": 0.0, "title": "Saturn: The Rings"}, {"start": 50.0, "title": "Titan"},
                  {"start": 100.0, "title": "The Moon: How the Moon Formed"}, {"start": 160.0, "title": "Tides"}]
    d = marathon.build_description("space & astronomy", segs, 9, ch, thumb_credits=["Thumbnail photo: Ann, CC BY 4.0"])
    assert "Chapters\n0:00 Saturn: The Rings\n0:50 Titan\n1:40 The Moon: How the Moon Formed" in d
    assert "- Saturn: https://youtu.be/s1" in d and "Thumbnail photo: Ann" in d and d.rstrip().endswith("#sleepdocumentary")
    many = [{"start": i * 60.0, "title": f"A fairly long chapter title number {i}"} for i in range(400)]
    assert len(marathon.build_description("space & astronomy", segs, 9, many).encode()) <= 4900


def test_available_runs_asks_github_per_run():
    calls = []

    def gh(*args):
        calls.append(args)
        rid = args[1].split("media-")[1].split("&")[0]
        return json.dumps({"artifacts": [{"expired": rid == "2"}] if rid in ("1", "2") else []})
    assert marathon.available_runs("o/r", _rows()[:3], gh=gh) == {"1"}
    assert len(calls) == 3


@pytest.mark.skipif(subprocess.run(["which", "ffmpeg"], capture_output=True).returncode != 0, reason="no ffmpeg")
def test_concat_joins_without_reencoding(tmp_path):
    parts = []
    for i, secs in enumerate((2, 3)):
        out = tmp_path / f"p{i}.mp4"
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", f"color=c=blue:s=320x180:r=10:d={secs}",
                        "-f", "lavfi", "-i", f"sine=f=440:d={secs}", "-c:v", "libx264", "-preset", "veryfast",
                        "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "160k", "-ar", "44100", "-ac", "2", "-shortest", str(out)],
                       check=True)
        parts.append(str(out))
    out = tmp_path / "m.mp4"
    subprocess.run(marathon.concat_cmd(marathon.write_concat_list(parts, str(tmp_path / "l.txt")), str(out)), check=True)
    assert abs(marathon.probe_seconds(str(out)) - 5.0) < 0.3


def test_thumb_credit_line_is_found():
    assert marathon.thumb_credit_from("a\nThumbnail photo: Ann, CC BY\n#sleep") == "Thumbnail photo: Ann, CC BY"
    assert marathon.thumb_credit_from("nothing") == ""
