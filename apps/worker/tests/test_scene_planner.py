"""scene_planner — montage cut-list shaping and dynamic kept-count (pure)."""

from __future__ import annotations

from storyfactory.services import scene_planner


# ---- shape_cut_list ----

def _total(cuts):
    return sum(e - s for s, e in cuts)


def test_shape_cut_list_preserves_valid_cuts_under_max():
    cuts = scene_planner.shape_cut_list(
        100.0, 200.0, [[100, 108], [120, 126]],
        target_seconds=18, max_seconds=20, min_seconds=8, max_cuts=6,
    )
    assert cuts == [[100.0, 108.0], [120.0, 126.0]]
    assert _total(cuts) == 14.0


def test_shape_cut_list_trims_to_max_seconds():
    cuts = scene_planner.shape_cut_list(
        0.0, 100.0, [[0, 15], [20, 35]],  # 30s total
        target_seconds=18, max_seconds=20, min_seconds=8, max_cuts=6,
    )
    assert _total(cuts) <= 20.0 + 1e-6


def test_shape_cut_list_fallback_when_no_cuts():
    cuts = scene_planner.shape_cut_list(
        50.0, 200.0, None,
        target_seconds=18, max_seconds=20, min_seconds=8, max_cuts=6,
    )
    assert cuts == [[50.0, 68.0]]  # single target window from scene start


def test_shape_cut_list_caps_number_of_cuts():
    raw = [[i * 10, i * 10 + 2] for i in range(10)]  # 10 tiny cuts
    cuts = scene_planner.shape_cut_list(
        0.0, 200.0, raw,
        target_seconds=18, max_seconds=20, min_seconds=2, max_cuts=3,
    )
    assert len(cuts) <= 3


def test_shape_cut_list_extends_when_under_min():
    cuts = scene_planner.shape_cut_list(
        0.0, 100.0, [[0, 3]],  # 3s, under min 8
        target_seconds=18, max_seconds=20, min_seconds=8, max_cuts=6,
    )
    assert _total(cuts) >= 8.0


def test_shape_cut_list_clamps_within_scene_bounds():
    cuts = scene_planner.shape_cut_list(
        10.0, 30.0, [[5, 40]],  # spills past both ends
        target_seconds=18, max_seconds=20, min_seconds=8, max_cuts=6,
    )
    assert cuts[0][0] >= 10.0 and cuts[0][1] <= 30.0


# ---- choose_kept_indices ----

def _scored(*pairs):
    """pairs of (index, importance, keep)."""
    return [{"index": i, "importance": imp, "keep": k} for i, imp, k in pairs]


def test_choose_kept_threshold():
    scored = _scored((0, 0.9, True), (1, 0.3, False), (2, 0.7, True))
    kept = scene_planner.choose_kept_indices(scored, threshold=0.6, min_count=1, max_count=10)
    assert kept == {0, 2}


def test_choose_kept_caps_to_max_by_importance():
    scored = _scored((0, 0.9, True), (1, 0.8, True), (2, 0.7, True))
    kept = scene_planner.choose_kept_indices(scored, threshold=0.6, min_count=1, max_count=2)
    assert kept == {0, 1}  # top-2 by importance


def test_choose_kept_tops_up_to_min():
    scored = _scored((0, 0.4, False), (1, 0.3, False), (2, 0.2, False))
    kept = scene_planner.choose_kept_indices(scored, threshold=0.6, min_count=1, max_count=10)
    assert kept == {0}  # nothing passes threshold, top-up the single highest
