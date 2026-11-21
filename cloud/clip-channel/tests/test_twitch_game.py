"""Game filter of the Twitch lane (no network: the API call and yt-dlp are replaced)."""
from datetime import datetime, timedelta, timezone

import pytest

from clipper import twitch


def fake_get(clips, games=None, search=None):
    def _get(path, params):
        if path == "users":
            return {"data": [{"id": "1", "display_name": "Kai"}]}
        if path == "clips":
            _get.params = dict(params)
            return {"data": clips}
        if path == "games":
            return {"data": (games or {}).get(params["name"], [])}
        if path == "search/categories":
            return {"data": search or []}
        raise AssertionError(path)
    return _get


def clip(i, views, game):
    return {"id": f"c{i}", "url": f"https://clips.twitch.tv/c{i}", "title": "t", "view_count": views, "duration": 20,
            "created_at": "2026-11-19T10:00:00Z", "video_id": "", "creator_name": "x", "game_id": game}


GTA = {"Grand Theft Auto VI": [{"id": 9, "name": "Grand Theft Auto VI"}]}


@pytest.fixture(autouse=True)
def keys(monkeypatch):
    monkeypatch.setattr(twitch, "creds", lambda: ("id", "secret"))


def test_only_the_games_clips_most_viewed_first(monkeypatch):
    monkeypatch.setattr(twitch, "_get", fake_get([clip(1, 50, "9"), clip(2, 900, "7"), clip(3, 70, "9")], games=GTA))
    clips, how = twitch.top_clips("kai", 7, game="Grand Theft Auto VI,GTA 6")
    assert [c["id"] for c in clips] == ["c3", "c1"] and how == "api"


def test_no_filter_keeps_every_game(monkeypatch):
    monkeypatch.setattr(twitch, "_get", fake_get([clip(1, 50, "9"), clip(2, 900, "7")]))
    assert [c["id"] for c in twitch.top_clips("kai", 7)[0]] == ["c2", "c1"]


def test_category_ids_need_the_api(monkeypatch):
    monkeypatch.setattr(twitch, "creds", lambda: ("", ""))
    with pytest.raises(RuntimeError):
        twitch.game_ids("Grand Theft Auto VI")


def test_game_ids_exact_name_then_search(monkeypatch):
    monkeypatch.setattr(twitch, "_get", fake_get([], games=GTA))
    assert twitch.game_ids("Grand Theft Auto VI,Grand Theft Auto 6") == {"9": "Grand Theft Auto VI"}
    monkeypatch.setattr(twitch, "_get", fake_get([], search=[{"id": "9", "name": "Grand Theft Auto VI"},
                                                              {"id": "3", "name": "Grand Theft Auto V"}]))
    assert twitch.game_ids("grand theft auto vi") == {"9": "Grand Theft Auto VI"}
    monkeypatch.setattr(twitch, "_get", fake_get([]))
    with pytest.raises(RuntimeError):
        twitch.game_ids("No Such Game")


def test_window_never_starts_before_release_day():
    now = datetime(2026, 11, 20, 15, tzinfo=timezone.utc)
    assert twitch.window_start(now, 7, "2026-11-19") == datetime(2026, 11, 19, tzinfo=timezone.utc)
    assert twitch.window_start(now, 7) == datetime(2026, 11, 13, 15, tzinfo=timezone.utc)
    assert twitch.window_start(datetime(2026, 12, 20, tzinfo=timezone.utc), 7, "2026-11-19") == datetime(2026, 12, 13, tzinfo=timezone.utc)


def test_prompt_takes_the_game_note():
    text = twitch.PLAN_PROMPT.format(name="Kai", days=7, period="week", blocks="b",
                                     game_note=twitch.GAME_NOTE.format(name="Kai", game="GTA 6"))
    assert "story cutscene" in text and "GTA 6" in text
    assert "cutscene" not in twitch.PLAN_PROMPT.format(name="Kai", days=7, period="week", blocks="b", game_note="")


# ---- without the API keys: each clip's own page says the game

def page_clip(i, views, created=0.0):
    return {"id": f"c{i}", "url": f"https://clips.twitch.tv/c{i}", "title": "t", "views": views, "duration": 20.0,
            "created": created, "vod": "", "vod_offset": None, "clipper": "", "streamer": "kai"}


def no_keys(monkeypatch, listed, pages):
    monkeypatch.setattr(twitch, "creds", lambda: ("", ""))
    monkeypatch.setattr(twitch, "clips_page", lambda login, days, limit=60: listed)

    def clip_page(url):
        page = pages[url.rsplit("/", 1)[-1]]
        if isinstance(page, Exception):
            raise page
        return page
    monkeypatch.setattr(twitch, "clip_page", clip_page)


def test_without_keys_the_clip_page_decides(monkeypatch):
    now = datetime.now(timezone.utc).timestamp()
    no_keys(monkeypatch, [page_clip(1, 900), page_clip(2, 800), page_clip(3, 700), page_clip(4, 600)],
            {"c1": {"game": "Just Chatting", "created": now, "clipper": "a"},
             "c2": {"game": "Grand Theft Auto VI", "created": now, "clipper": "b"},
             "c3": RuntimeError("gone"),
             "c4": {"game": "grand theft auto  vi", "created": now, "clipper": ""}})
    clips, how = twitch.top_clips("kai", 7, game="Grand Theft Auto VI,GTA 6")
    assert [c["id"] for c in clips] == ["c2", "c4"] and how == "clip pages"
    assert clips[0]["clipper"] == "b" and clips[0]["game"] == "Grand Theft Auto VI"


def test_without_keys_nothing_from_before_the_release_day(monkeypatch):
    now = datetime.now(timezone.utc)
    since = now.strftime("%Y-%m-%d")
    old = (now - timedelta(days=2)).timestamp()
    gta = {"game": "Grand Theft Auto VI", "clipper": ""}
    no_keys(monkeypatch, [page_clip(1, 900, old), page_clip(2, 800), page_clip(3, 700), page_clip(4, 600)],
            {"c1": AssertionError("an old clip is not looked up"),
             "c2": {**gta, "created": old},                      # the listing had no date, the page has: too old
             "c3": {**gta, "created": 0.0},                      # no date anywhere: left out
             "c4": {**gta, "created": now.timestamp() + 60}})
    assert [c["id"] for c in twitch.top_clips("kai", 7, game="GTA 6,Grand Theft Auto VI", since=since)[0]] == ["c4"]


def test_api_failure_falls_back_to_the_clip_pages(monkeypatch):
    now = datetime.now(timezone.utc).timestamp()
    no_keys(monkeypatch, [page_clip(1, 900)], {"c1": {"game": "GTA 6", "created": now, "clipper": ""}})
    monkeypatch.setattr(twitch, "creds", lambda: ("id", "secret"))

    def boom(path, params):
        raise RuntimeError("401")
    monkeypatch.setattr(twitch, "_get", boom)
    assert twitch.top_clips("kai", 7, game="GTA 6")[1] == "clip pages"


def test_without_keys_old_clips_do_not_use_up_the_look(monkeypatch):
    now = datetime.now(timezone.utc)
    old = (now - timedelta(days=2)).timestamp()
    gta = {"game": "Grand Theft Auto VI", "clipper": "", "created": now.timestamp() + 60}
    # 50 well-viewed clips from before the release day, then two new ones with fewer views
    listing = [page_clip(i, 5000 - i, old) for i in range(50)] + [page_clip(100, 90), page_clip(101, 80)]
    no_keys(monkeypatch, listing, {"c100": gta, "c101": gta})
    got = twitch.top_clips("kai", 7, game="Grand Theft Auto VI", since=now.strftime("%Y-%m-%d"))[0]
    assert [c["id"] for c in got] == ["c100", "c101"]


def test_general_ranking_leaves_out_game_only_clips(monkeypatch):
    pages = {"a": "Just Chatting", "b": "Grand Theft Auto VI", "c": "grand theft auto 6", "d": "Grand Theft Auto III",
             "f": "IRL"}

    def page(url):
        key = url[-1]
        if key == "e":
            raise RuntimeError("no page")
        return {"game": pages[key], "created": 0.0, "clipper": ""}
    monkeypatch.setattr(twitch, "clip_page", page)
    clips = [{"id": k, "url": f"https://clips.twitch.tv/{k}"} for k in "abcdef"]
    kept = twitch.outside_games(clips)
    assert [c["id"] for c in kept] == ["a", "d", "f"]          # GTA 6 clips and the unreadable one are out
    assert [c["id"] for c in twitch.outside_games(clips, want=2)] == ["a", "d"]
    assert twitch.outside_games(clips, look=1) == [{**clips[0], "game": "Just Chatting"}]


def test_general_prompt_has_the_cutscene_rule():
    note = twitch.GENERAL_NOTE.format(name="Kai")
    assert "cutscene" in note and "not out yet" in note


def _c(i, title, created, vod="", off=None):
    return {"id": f"c{i}", "title": title, "created": created, "vod": vod, "vod_offset": off}


def test_one_title_twice_within_twenty_minutes_is_one_moment():
    a, b = _c(1, "HE TRYNA IGNORE IT", 1000.0), _c(2, "He tryna ignore it!", 1000.0 + 600)
    assert twitch.same_moment(a, b) and [c["id"] for c in twitch.distinct([a, b])] == ["c1"]
    # a joke title reused an hour later is another moment, and so is another title ten minutes later
    assert not twitch.same_moment(_c(1, "copyright test", 1000.0), _c(2, "copyright test", 1000.0 + 3600))
    assert not twitch.same_moment(_c(1, "ninja", 1000.0), _c(2, "AURA", 1000.0 + 600))
    # no title, or no date on one of them: only the old rules apply
    assert not twitch.same_moment(_c(1, "", 1000.0), _c(2, "", 1000.0 + 600))
    assert not twitch.same_moment(_c(1, "same", 0.0), _c(2, "same", 600.0))
    assert twitch.same_moment(_c(1, "x", 1000.0), _c(2, "y", 1100.0))
