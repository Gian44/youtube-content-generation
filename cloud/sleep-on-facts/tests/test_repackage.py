from sof import repackage

DESC = """Fall asleep to 3 hours of calm space facts: The Moon. Tonight: a day and a night with The Moon. Settle in.

Chapters
0:00 1. Dawn Over the Near Side
8:26 2. The First Light of Its Making
15:59 3. The Cooling World Beneath the Light
1:02:05 4. Three Quiet Things to Keep

Facts in this video are drawn from the public sources below.

Sources (Wikipedia, CC BY-SA 4.0):
- Moon https://en.wikipedia.org/wiki/Moon

Photos: Wikimedia Commons contributors, NASA, Pexels and Pixabay.

Thumbnail photo: Ann, CC BY 4.0, via Wikimedia Commons — https://commons.wikimedia.org/wiki/File:Moon.jpg

#sleep #spacefacts #TheMoon"""


def test_chapter_rows_read_the_block_only():
    rows = repackage.chapter_rows(DESC)
    assert rows[0] == ("0:00", "1. Dawn Over the Near Side") and rows[-1] == ("1:02:05", "4. Three Quiet Things to Keep")
    assert len(rows) == 4 and repackage.chapter_rows("no chapters here") == []


def test_rewrite_renames_chapters_and_swaps_the_thumbnail_credit():
    new = repackage.rewrite_description(DESC, chapters=["Sunrise on the Near Side", "How the Moon Formed",
                                                        "3. The Cooling Crust", "Three Quiet Things to Keep"],
                                        thumb_credit="Thumbnail photo: Bob, CC BY-SA 4.0, via Wikimedia Commons")
    assert "0:00 Sunrise on the Near Side\n8:26 How the Moon Formed\n15:59 The Cooling Crust\n1:02:05 Three Quiet" in new
    assert "Ann, CC BY 4.0" not in new and new.count("Thumbnail photo:") == 1
    assert new.endswith("Thumbnail photo: Bob, CC BY-SA 4.0, via Wikimedia Commons\n\n#sleep #spacefacts #TheMoon")
    assert "Sources (Wikipedia" in new and "\n\n\n" not in new


def test_rewrite_keeps_chapters_when_the_count_is_wrong_and_can_drop_the_credit():
    new = repackage.rewrite_description(DESC, chapters=["Only one"], thumb_credit="")
    assert "0:00 1. Dawn Over the Near Side" in new and "Thumbnail photo:" not in new
    assert new.endswith("#sleep #spacefacts #TheMoon") and "\n\n\n" not in new
    assert repackage.rewrite_description(DESC) == DESC                  # nothing asked → nothing changes


def test_summary_for_prompt_stops_before_sources():
    s = repackage.summary_for_prompt(DESC)
    assert s.startswith("Fall asleep to 3 hours") and "Chapters" in s and "Sources" not in s and "Thumbnail" not in s


class FakeLLM:
    def __init__(self, data):
        self.data, self.prompts = data, []

    def generate_json(self, prompt, **kw):
        self.prompts.append(prompt)
        return self.data


def test_write_packaging_cleans_the_model_output():
    llm = FakeLLM({"title_hook": "The Moon Is Slowly Drifting Away From Earth", "thumb_text": "drifting away",
                   "chapters": ["A", "B", "C", "D"]})
    pack = repackage.write_packaging(llm, topic="The Moon", description=DESC, model="fast")
    assert pack == {"title_hook": "The Moon Is Slowly Drifting Away From Earth", "thumb_text": "DRIFTING AWAY",
                    "chapters": ["A", "B", "C", "D"]}
    assert "4. Three Quiet Things to Keep" in llm.prompts[0] and "{{" not in llm.prompts[0]
    bad = repackage.write_packaging(FakeLLM({"title_hook": "The Scariest Moon!", "thumb_text": "SHOCKING", "chapters": ["A"]}),
                                    topic="The Moon", description=DESC, model="fast")
    assert bad == {"title_hook": "", "thumb_text": "", "chapters": []}


def test_mark_ledger_records_only_applied_rows():
    rows = [{"video_id": "a", "title": "old a"}, {"video_id": "b", "title": "old b"}]
    reps = [{"video_id": "a", "applied": True, "title_before": "old a", "title": "new a", "thumb_text": "X"},
            {"video_id": "b", "applied": False, "title_before": "old b", "title": "new b", "thumb_text": "Y"}]
    out = repackage.mark_ledger(rows, reps, today="2026-10-25")
    assert out[0]["title"] == "new a" and out[0]["repackaged"] == {"date": "2026-10-25", "title_before": "old a", "thumb_text": "X"}
    assert out[1] == {"video_id": "b", "title": "old b"}


def test_apply_prepared_publishes_the_reviewed_files(tmp_path):
    import json as _json
    vdir = tmp_path / "abc"
    vdir.mkdir()
    (vdir / "meta.json").write_text(_json.dumps({"video_id": "abc", "title": "New | 3 Hours of Space Facts to Fall Asleep To",
                                                 "thumb_text": "X", "title_before": "?"}))
    (vdir / "description.txt").write_text("new description")
    (vdir / "thumb.jpg").write_bytes(b"jpg")
    calls = []

    class Req:
        def __init__(self, out): self.out = out
        def execute(self): return self.out

    class Videos:
        def list(self, **kw): return Req({"items": [{"snippet": {"title": "Old", "tags": ["sleep"]}}]})
        def update(self, **kw): calls.append(("update", kw["body"])); return Req({})

    class Thumbs:
        def set(self, **kw): calls.append(("thumb", kw)); return Req({})

    class YT:
        def videos(self): return Videos()
        def thumbnails(self): return Thumbs()
    rep = repackage.apply_prepared(YT(), vdir)
    assert rep["applied"] and rep["title_before"] == "Old"
    body = calls[0][1]
    assert body["id"] == "abc" and body["snippet"]["title"].startswith("New |") and body["snippet"]["description"] == "new description"
    assert body["snippet"]["tags"] == ["sleep"] and calls[1] == ("thumb", {"videoId": "abc", "media_body": str(vdir / "thumb.jpg")})
