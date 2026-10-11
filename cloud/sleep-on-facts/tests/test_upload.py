from sof.upload import video_body, upload_video, health, ensure_playlist, add_to_playlist, upload_state, wait_until_accepted


def test_video_body():
    b = video_body(title="T", description="D", tags=["a"], privacy="public")
    assert b["snippet"]["categoryId"] == "27" and b["status"]["privacyStatus"] == "public"      # Education
    assert b["snippet"]["defaultAudioLanguage"] == "en" and "publishAt" not in b["status"]
    assert b["status"]["selfDeclaredMadeForKids"] is False and "containsSyntheticMedia" not in b["status"]


def test_video_body_scheduled_is_private_with_publish_at():
    b = video_body(title="T", description="D", tags=[], privacy="public", publish_at="2026-10-12T00:30:00Z")
    assert b["status"]["privacyStatus"] == "private" and b["status"]["publishAt"] == "2026-10-12T00:30:00Z"


class FakePlaylists:
    """Just enough of the YouTube client for playlists()/playlistItems()."""
    def __init__(self, existing=None, items=None):
        self.existing, self.items, self.inserted, self._op = existing or [], items or [], [], None

    def playlists(self): self._kind = "pl"; return self
    def playlistItems(self): self._kind = "pi"; return self
    def list(self, **kw): self._op = ("list", self._kind); return self
    def list_next(self, req, resp): return None
    def insert(self, **kw): self._op = ("insert", self._kind); self.inserted.append((self._kind, kw)); return self

    def execute(self):
        if self._op == ("list", "pl"):
            return {"items": [{"id": i, "snippet": {"title": t}} for i, t in self.existing]}
        if self._op == ("list", "pi"):
            return {"items": [{"snippet": {"resourceId": {"videoId": v}}} for v in self.items]}
        return {"id": "PLnew"}


def test_ensure_playlist_reuses_or_creates():
    yt = FakePlaylists(existing=[("PL1", "Space Facts to Fall Asleep To")])
    assert ensure_playlist(yt, "space facts to fall asleep to") == "PL1" and not yt.inserted
    yt = FakePlaylists()
    assert ensure_playlist(yt, "History for Sleep", "d") == "PLnew"
    kind, kw = yt.inserted[0]
    assert kind == "pl" and kw["body"]["status"]["privacyStatus"] == "public"


def test_add_to_playlist_is_idempotent():
    yt = FakePlaylists(items=["vid1"])
    add_to_playlist(yt, "PL1", "vid1")
    assert not yt.inserted
    add_to_playlist(yt, "PL1", "vid2")
    assert yt.inserted[0][1]["body"]["snippet"]["resourceId"]["videoId"] == "vid2"


class FakeYT:
    def __init__(self):
        self.thumb = None

    def videos(self): return self
    def thumbnails(self): return self
    def channels(self): return self
    def list(self, **kw): return self
    def insert(self, **kw): self.kw = kw; return self
    def set(self, **kw): self.thumb = kw; return self
    def next_chunk(self): return None, {"id": "vid123"}
    def execute(self): return {"items": [{"id": "UC1", "snippet": {"title": "Sleep On Facts"}}]}


def test_upload_returns_id_and_sets_thumbnail(tmp_path):
    f = tmp_path / "f.mp4"; f.write_bytes(b"x")
    t = tmp_path / "t.jpg"; t.write_bytes(b"y")
    yt = FakeYT()
    assert upload_video(yt, str(f), video_body(title="T", description="D", tags=[], privacy="public"), str(t)) == "vid123"
    assert yt.thumb["videoId"] == "vid123"


def test_health_returns_channel_title():
    assert health(FakeYT()) == "Sleep On Facts"


def test_upload_retries_dropped_connections(tmp_path):
    import ssl

    class FlakyYT(FakeYT):
        calls = 0

        def next_chunk(self):
            FlakyYT.calls += 1
            if FlakyYT.calls <= 2:
                raise ssl.SSLEOFError("EOF occurred in violation of protocol")
            return None, {"id": "vid456"}

    f = tmp_path / "f.mp4"; f.write_bytes(b"x")
    slept = []
    assert upload_video(FlakyYT(), str(f), video_body(title="T", description="D", tags=[], privacy="public"), None,
                        sleep=slept.append) == "vid456"
    assert FlakyYT.calls == 3 and slept == [2, 4]


def test_add_to_new_playlist_tolerates_404_then_retries():
    from googleapiclient.errors import HttpError

    class Resp(dict):
        status = 404
        reason = "Not Found"

    class NewPlaylistYT(FakePlaylists):
        inserts = 0

        def execute(self):
            if self._op == ("list", "pi"):
                raise HttpError(Resp(), b"playlistNotFound")
            if self._op == ("insert", "pi"):
                NewPlaylistYT.inserts += 1
                if NewPlaylistYT.inserts == 1:
                    raise HttpError(Resp(), b"playlistNotFound")
            return {"id": "x"}

    slept = []
    add_to_playlist(NewPlaylistYT(), "PLnew", "vid9", sleep=slept.append)
    assert NewPlaylistYT.inserts == 2 and slept == [5]


class FakeStatusYT:
    """videos().list(...) answers from a script of responses, one per poll (the last one repeats)."""
    def __init__(self, *responses):
        self.responses, self.polls = list(responses), 0

    def videos(self): return self
    def list(self, **kw): self.kw = kw; return self

    def execute(self):
        r = self.responses[min(self.polls, len(self.responses) - 1)]
        self.polls += 1
        return {"items": [{"id": vid, "status": st} for vid, st in r.items()]}


def test_upload_state_reads_rejections_failures_and_missing_videos():
    yt = FakeStatusYT({"a": {"uploadStatus": "rejected", "rejectionReason": "length"},
                       "b": {"uploadStatus": "processed"}, "d": {"uploadStatus": "failed", "failureReason": "conversion"}})
    assert upload_state(yt, ["a", "b", "c", "d"]) == {"a": ("rejected", "length"), "b": ("processed", ""),
                                                      "c": ("missing", ""), "d": ("failed", "conversion")}
    assert yt.kw["id"] == "a,b,c,d" and "status" in yt.kw["part"]


def _wait(yt, **kw):
    naps = []
    return wait_until_accepted(yt, "v", timeout=90, every=30, sleep=naps.append, **kw), naps


def test_wait_returns_as_soon_as_youtube_rejects():
    (state, reason), naps = _wait(FakeStatusYT({"v": {"uploadStatus": "uploaded"}},
                                               {"v": {"uploadStatus": "rejected", "rejectionReason": "length"}}))
    assert (state, reason) == ("rejected", "length") and naps == [30]


def test_wait_accepts_a_video_still_processing_at_the_deadline():
    (state, _), naps = _wait(FakeStatusYT({"v": {"uploadStatus": "uploaded"}}))
    assert state == "uploaded" and naps == [30, 30, 30]


def test_wait_flags_a_video_that_vanishes_after_upload():
    (state, reason), _ = _wait(FakeStatusYT({"v": {"uploadStatus": "uploaded"}}, {}))
    assert state == "missing" and "disappeared" in reason


def test_wait_tolerates_a_brief_miss_right_after_insert():
    (state, _), _ = _wait(FakeStatusYT({}, {"v": {"uploadStatus": "uploaded"}}, {"v": {"uploadStatus": "processed"}}))
    assert state == "processed"


def test_wait_flags_a_video_never_seen():
    (state, reason), naps = _wait(FakeStatusYT({}))
    assert state == "missing" and "not visible" in reason and naps == [30, 30, 30]
