"""scene_detector — shot-boundary parsing and dialogue scene grouping (pure)."""

from __future__ import annotations

from storyfactory.services import scene_detector


def _w(word, start, end):
    return {"word": word, "start": start, "end": end}


def test_parse_showinfo_times():
    stderr = (
        "[Parsed_showinfo] n:0 pts_time:0.000 ...\n"
        "[Parsed_showinfo] n:1 pts_time:12.5 ...\n"
        "garbage line\n"
        "[Parsed_showinfo] n:2 pts_time:30 ...\n"
    )
    assert scene_detector._parse_showinfo_times(stderr) == [0.0, 12.5, 30.0]


def test_group_dialogue_scenes_splits_on_gap():
    words = [
        _w("hello", 0.0, 0.5), _w("there", 0.6, 1.0),   # scene 1
        _w("much", 10.0, 10.4), _w("later", 10.5, 11.0),  # gap > 1.2 -> scene 2
    ]
    scenes = scene_detector.group_dialogue_scenes(words, min_gap=1.2, min_scene_seconds=0.1)
    assert len(scenes) == 2
    assert scenes[0]["text"] == "hello there"
    assert scenes[1]["start"] == 10.0


def test_group_dialogue_scenes_merges_short_scene_forward():
    words = [
        _w("a", 0.0, 0.2),                  # tiny scene 1 (<min)
        _w("b", 5.0, 9.0), _w("c", 9.1, 12.0),  # scene 2 (long)
    ]
    scenes = scene_detector.group_dialogue_scenes(words, min_gap=1.2, min_scene_seconds=2.0)
    # scene 1 is too short and is merged into the next
    assert len(scenes) == 1
    assert scenes[0]["start"] == 0.0
    assert scenes[0]["end"] == 12.0


def test_build_candidate_scenes_snaps_to_shot_boundaries():
    words = [_w("x", 1.3, 5.0), _w("y", 5.1, 9.8)]
    # boundaries near the scene edges
    cands = scene_detector.build_candidate_scenes(
        words, shot_boundaries=[1.0, 10.0], duration=60.0, min_scene_seconds=0.1
    )
    assert len(cands) == 1
    assert cands[0]["start"] == 1.0   # snapped from 1.3 -> 1.0
    assert cands[0]["end"] == 10.0    # snapped from 9.8 -> 10.0
    assert cands[0]["index"] == 0


def test_build_candidate_scenes_clamps_to_duration():
    words = [_w("x", 1.0, 50.0)]
    cands = scene_detector.build_candidate_scenes(
        words, shot_boundaries=[], duration=30.0, min_scene_seconds=0.1
    )
    assert cands[0]["end"] == 30.0
