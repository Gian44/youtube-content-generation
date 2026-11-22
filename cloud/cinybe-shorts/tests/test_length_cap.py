"""15-minute cap: an unverified channel's uploads over 15:00 are abandoned by YouTube (both Plan 9 runs, 2026-11-22)."""
import pytest

from cny import planner
from cny.config import Config, SLOWEST_WPM
from tests.test_planner_seo import FILM, _acts, _scripts


def test_capped_target_fits_under_15_minutes_even_at_the_slowest_pace():
    cfg = Config()
    assert cfg.max_long_words and cfg.target_words < cfg.max_long_words
    assert cfg.max_long_words / SLOWEST_WPM <= 14.5
    assert cfg.max_long_seconds < 900
    lo, hi = cfg.acts_range
    assert 9 <= lo <= hi <= 14


def test_cap_off_restores_the_17_minute_target(monkeypatch):
    monkeypatch.setenv("CNY_MAX_LONG_MINUTES", "0")
    cfg = Config.from_env()
    assert cfg.max_long_words is None and cfg.max_long_seconds is None
    assert cfg.target_words == int(17 * cfg.words_per_minute)


def test_validate_scripts_rejects_over_the_hard_ceiling_with_a_cut_message():
    acts = _acts()
    planner.validate_scripts(_scripts(acts), acts, film=FILM, target_words=300, shorts_per_film=3, short_max_words=80)
    with pytest.raises(planner.PlanError, match=r"over the maximum of 250.*Cut at least"):
        planner.validate_scripts(_scripts(acts), acts, film=FILM, target_words=300, shorts_per_film=3, short_max_words=80,
                                 max_words=250)
