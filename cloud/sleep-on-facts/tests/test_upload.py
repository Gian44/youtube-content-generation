from sof.upload import video_body, upload_video, health


def test_video_body():
    b = video_body(title="T", description="D", tags=["a"], privacy="public")
    assert b["snippet"]["categoryId"] == "24" and b["status"]["privacyStatus"] == "public"
    assert b["status"]["selfDeclaredMadeForKids"] is False and b["status"]["containsSyntheticMedia"] is True


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
