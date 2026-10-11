import io
import json
import math
import random
import re
from pathlib import Path

import httpx
import pytest

from sof import images, tts, visuals as V

# ---------------------------------------------------------------- shared fixtures

RINGS = ("Saturn's rings are mostly made of water ice, in pieces as small as grains of sand and as large as houses. "
         "Seen from Earth through a small telescope, they look like a single smooth band of light. Up close they break "
         "apart into thousands of narrow ringlets. The broad A ring and the bright B ring are separated by the Cassini "
         "Division, a dark gap about four thousand eight hundred kilometres wide. It was first noticed by the astronomer "
         "Giovanni Cassini in the year 1675. The gap is not truly empty. It holds thin, faint ringlets of darker material, "
         "quietly orbiting in the shadow of the brighter rings.\n\nFarther out, within the A ring, lies the narrow Encke "
         "Gap. A tiny moon called Pan travels inside it, sweeping the gap clear as it goes, and leaving delicate waves in "
         "the ice on either side. The rings are astonishingly thin. In most places they are only about ten metres deep, "
         "though they stretch for hundreds of thousands of kilometres.")
MOON = ("Titan is the largest moon of Saturn, and the second largest moon in the whole solar system. It is wrapped in a "
        "thick orange haze that hides its surface from ordinary cameras. Beneath that haze lie lakes and seas of liquid "
        "methane and ethane, the only stable bodies of surface liquid known anywhere beyond Earth. The largest of them, "
        "Kraken Mare, is bigger than the Caspian Sea. Rain falls slowly on Titan, in heavy drops that drift down through "
        "the dense cold air.\n\nIn January 2005 the Huygens probe descended through the haze on a parachute. For more "
        "than two hours it drifted downward, photographing rounded hills and dark channels carved by flowing liquid. It "
        "came to rest on a damp plain scattered with smooth pebbles of water ice. The probe kept sending signals for "
        "about seventy minutes after landing, until its batteries finally faded.")
FOUNTAINS = ("Enceladus is a small, bright moon, only about five hundred kilometres across. Its surface reflects almost "
             "all of the sunlight that reaches it, which makes it one of the whitest objects in the solar system. Near "
             "its south pole run four long, parallel fractures, nicknamed the tiger stripes. From these cracks, tall "
             "fountains of water vapour and ice grains rise high into space. Some of that ice falls back as fresh snow, "
             "and some of it drifts outward to feed the faint, wide E ring.\n\nThe Cassini spacecraft flew through these "
             "plumes several times, tasting the spray with its instruments. It found salt, silica and simple organic "
             "molecules, signs of a hidden ocean of liquid water beneath the icy crust. Far below the surface, warm "
             "water may be moving slowly across the rocky sea floor.")
SECTIONS = [{"heading": "The Rings", "body": RINGS}, {"heading": "The Great Moon", "body": MOON},
            {"heading": "Ice Fountains", "body": FOUNTAINS}]


def _item(name, wiki, kind, *quotes):
    return {"name": name, "wiki_title": wiki, "kind": kind, "quotes": list(quotes)}


CANNED = {
    "The Rings": {"items": [
        _item("Cassini Division", "Cassini Division", "celestial", "separated by the Cassini Division, a dark gap",
              "The gap is not truly empty", "the Cassini Division shines like a silver river"),   # 3rd: hallucinated
        _item("Saturn", "Saturn", "planet", "Saturn's rings are mostly made of water ice"),      # the topic itself
        _item("Encke Gap", "Encke Gap", "celestial", "within the A ring, lies the narrow Encke Gap"),
        _item("Pan", "Pan (moon)", "moon", "A tiny moon called Pan travels inside it"),
        _item("Giovanni Cassini", "Giovanni Domenico Cassini", "person", "noticed by the astronomer Giovanni Cassini"),
    ]},
    "The Great Moon": {"items": [
        _item("Titan", "Titan (moon)", "moon", "Titan is the largest moon of Saturn", "Rain falls slowly on Titan"),
        _item("Kraken Mare", "Kraken Mare", "water", "The largest of them, Kraken Mare, is bigger"),
        _item("Ligeia Mare", "Ligeia Mare", "water", "Ligeia Mare glitters in the dark"),        # only hallucinations
        _item("Huygens probe", "Huygens (spacecraft)", "spacecraft", "the Huygens probe descended through the haze"),
    ]},
    "Ice Fountains": {"items": [
        _item("Enceladus", "Enceladus", "moon", "Enceladus is a small, bright moon"),
        _item("tiger stripes", "Tiger stripes (Enceladus)", "landform", "nicknamed the tiger stripes"),
        _item("Cassini spacecraft", "Cassini–Huygens", "spacecraft", "The Cassini spacecraft flew through these plumes"),
    ]},
}


class FakeLLM:
    """Canned JSON per section heading; a heading listed in ``fail`` raises like a dead API."""

    def __init__(self, canned, fail=()):
        self.canned, self.fail, self.calls = canned, set(fail), []

    def generate_json(self, prompt, *, system=None, max_tokens=3000, temperature=0.7, model=None, reasoning="low"):
        heading = re.search(r'titled "([^"]*)"', prompt).group(1)
        self.calls.append({"heading": heading, "model": model, "prompt": prompt})
        if heading in self.fail:
            raise RuntimeError("HTTP 500")
        return json.loads(json.dumps(self.canned.get(heading, {"items": []})))


# ---------------------------------------------------------------- 1. extract_mentions

def test_extract_mentions_keeps_only_verbatim_quotes_and_drops_the_topic():
    llm = FakeLLM(CANNED)
    rows = V.extract_mentions(llm, SECTIONS, topic="Saturn", model="cheap-model", max_workers=3)
    assert {c["heading"] for c in llm.calls} == {"The Rings", "The Great Moon", "Ice Fountains"}
    assert all(c["model"] == "cheap-model" for c in llm.calls)
    rings_prompt = next(c["prompt"] for c in llm.calls if c["heading"] == "The Rings")
    assert "Saturn" in rings_prompt and RINGS in rings_prompt and "{{" not in rings_prompt

    assert all(set(r) == {"key", "name", "wiki_title", "kind", "section", "quote", "pos", "rank"} for r in rows)
    keys = [(r["section"], r["key"]) for r in rows]
    assert keys == [(0, "cassini division"), (0, "cassini division"), (0, "encke gap"), (0, "pan"),
                    (0, "giovanni domenico cassini"),
                    (1, "titan"), (1, "titan"), (1, "kraken mare"), (1, "huygens"),
                    (2, "enceladus"), (2, "tiger stripes"), (2, "cassini huygens")]
    quotes = [r["quote"] for r in rows]
    assert "the Cassini Division shines like a silver river" not in quotes          # hallucinated quote dropped
    assert not any(r["name"] in ("Ligeia Mare", "Saturn") for r in rows)          # no valid quote / the topic itself
    assert [r["rank"] for r in rows if r["section"] == 0] == [0, 0, 1, 2, 3]       # the topic used no rank slot
    assert all(0 <= r["pos"] <= 1 for r in rows)
    titan = [r for r in rows if r["key"] == "titan"]
    assert titan[0]["kind"] == "celestial" and titan[0]["pos"] < titan[1]["pos"]
    assert V.extract_mentions(FakeLLM(CANNED), SECTIONS, topic="Saturn", max_workers=1) == rows   # deterministic


def test_extract_mentions_survives_a_failing_section_and_merges_names_across_sections():
    canned = {**CANNED, "Again": {"items": [_item("Titan", "", "Moons", "Far out, Titan glows faintly orange")]}}
    secs = SECTIONS + [{"heading": "Again", "body": "Far out, Titan glows faintly orange in the sunlight."},
                       {"heading": "Empty", "body": "   "}]
    llm = FakeLLM(canned, fail={"The Great Moon"})
    rows = V.extract_mentions(llm, secs, topic="Saturn", model=None)
    assert not any(r["section"] == 1 for r in rows)                                 # failed section → nothing
    again = [r for r in rows if r["section"] == 3]
    assert [r["key"] for r in again] == ["titan"] and again[0]["kind"] == "celestial"   # untitled "Titan" → "titan"
    assert "Empty" not in {c["heading"] for c in llm.calls}                         # empty section: no call
    garbage = FakeLLM({"The Rings": {"items": "not a list"}, "The Great Moon": {"nope": 1},
                       "Ice Fountains": {"items": [None, 3, {"name": "", "quotes": ["x"]}, {"name": "Enceladus"}]}})
    assert V.extract_mentions(garbage, SECTIONS, topic="Saturn") == []


def test_keys_kinds_and_matching_helpers():
    assert V.entity_key("Titan", "Titan (moon)") == V.entity_key("Titan") == "titan"
    assert V.entity_key("x", "Cassini–Huygens") == "cassini huygens" and V.entity_key("The Great Pyramid") == "great pyramid"
    assert V.entity_key("Château de Chambord") == "chateau de chambord"
    assert [V._kind(k) for k in ("Moon", "natural satellite", "temple", "species", "", "Space probe", "statues")] == \
        ["celestial", "celestial", "building", "species", "other", "spacecraft", "artwork"]
    assert V._find_words("the titanic and titan", "titan") == 16 and V._find_words("titanic", "titan") == -1
    text, idx = V._norm_map("Saturn’s rings — Cassini–Huygens!")
    assert text == "saturn s rings cassini huygens" and len(idx) == len(text) and idx[text.index("cassini")] == 17


# ---------------------------------------------------------------- 2. mention_times

def _chunked(limit=300, cps=15.0):
    chunks, owner = tts.chunk_sections(SECTIONS, limit)
    durs = [len(c) / cps for c in chunks]
    return chunks, owner, durs


def test_mention_times_find_each_quote_in_its_own_chunk():
    chunks, owner, durs = _chunked()
    rows = V.mention_times(V.extract_mentions(FakeLLM(CANNED), SECTIONS, topic="Saturn"),
                           chunks=chunks, owner=owner, chunk_durations=durs)
    assert all(r["timing"] == "quote" for r in rows)
    assert [r["time"] for r in rows] == sorted(r["time"] for r in rows)
    starts = [sum(durs[:i]) for i in range(len(chunks))]
    for r in rows:
        name = "Kraken Mare" if r["key"] == "kraken mare" else None
        if name:      # the time is when the NAME is spoken inside the quote, from its own chunk
            ci = next(i for i, c in enumerate(chunks) if name in c)
            assert r["time"] == pytest.approx(starts[ci] + chunks[ci].index(name) / len(chunks[ci]) * durs[ci])
        ci = max(i for i in range(len(chunks)) if starts[i] <= r["time"] + 1e-9)
        assert owner[ci] == r["section"]                                            # inside its own section
    titan = [r["time"] for r in rows if r["key"] == "titan"]
    ci = next(i for i, c in enumerate(chunks) if c.startswith("Titan is the largest"))
    assert titan[0] == pytest.approx(starts[ci]) and titan[1] > titan[0]


def test_mention_times_quote_across_chunk_boundary_estimate_and_errors():
    secs = [{"heading": "A", "body": "Beyond the rings lies Titan. It is the largest moon of Saturn. Its haze is orange."},
            {"heading": "B", "body": "The Cassini Division is a dark gap. Mimas shapes it."}]
    chunks, owner = tts.chunk_sections(secs, 40)
    durs = [len(c) / 10 for c in chunks]
    ms = [{"key": "titan", "name": "Titan", "wiki_title": "Titan (moon)", "section": 0, "quote": "beyond the rings lies TITAN!"},
          {"key": "gap", "name": "Cassini Division", "wiki_title": "", "section": 1, "quote": "is a dark gap. Mimas"},
          {"key": "mimas", "name": "Mimas", "wiki_title": "", "section": 1, "quote": "a quote that is not there", "pos": 0.5},
          {"key": "ghost", "name": "Ghost", "wiki_title": "", "section": 7, "quote": "anything"}]
    out = {r["key"]: r for r in V.mention_times(ms, chunks=chunks, owner=owner, chunk_durations=durs)}
    assert out["titan"]["time"] == pytest.approx(2.2) and out["titan"]["timing"] == "quote"   # 22/28 into a 2.8 s chunk
    assert out["gap"]["time"] == pytest.approx(10.1) and out["gap"]["timing"] == "quote"     # spans two chunks: 8.0 + 21/35·3.5
    sec_b = (8.0, 8.0 + 3.5 + 1.6)
    assert out["mimas"]["timing"] == "estimate" and out["mimas"]["time"] == pytest.approx(sum(sec_b) / 2)
    assert "ghost" not in out                                                       # its section has no audio
    with pytest.raises(ValueError):
        V.mention_times(ms, chunks=chunks, owner=owner, chunk_durations=durs[:-1])


# ---------------------------------------------------------------- 3. resolve_titles

def _wiki_transport(calls, fail=False):
    known = {"Titan (moon)": "Titan (moon)", "Cassini (spacecraft)": "Cassini–Huygens", "Cassini–Huygens": "Cassini–Huygens",
             "Saturn": "Saturn", "Encke Gap": "Encke Gap", "Titan": "Titan"}

    def handler(req):
        calls.append(dict(req.url.params))
        if fail:
            return httpx.Response(503)
        p = req.url.params
        if p.get("list") == "search":
            q = p["srsearch"]
            hits = {"Kraken Sea Saturn": ["Kraken Mare"], "Titan Saturn": ["Titan (disambiguation)", "Saturn", "Titan (moon)"],
                    "Mystery Saturn": ["Saturn"]}.get(q, [])
            return httpx.Response(200, json={"query": {"search": [{"title": t} for t in hits]}})
        titles = p["titles"].split("|")
        redirects = [{"from": "Cassini (spacecraft)", "to": "Cassini–Huygens"},
                     {"from": "Great White Spot", "to": "Saturn", "tofragment": "Great White Spot"}]
        pages = []
        for t in titles:
            if t == "Great White Spot":
                continue
            if t in known:
                page = {"title": known[t]}
                if t == "Titan":
                    page["pageprops"] = {"disambiguation": ""}
                pages.append(page)
            else:
                pages.append({"title": t, "missing": True})
        pages.append({"title": "Saturn"})
        return httpx.Response(200, json={"query": {"redirects": [r for r in redirects if r["from"] in titles],
                                                   "pages": pages}})
    return httpx.MockTransport(handler)


def test_resolve_titles_merges_redirects_fixes_ambiguous_titles_and_drops_the_topic():
    ms = [{"key": "cassini", "name": "Cassini", "wiki_title": "Cassini (spacecraft)", "section": 0, "quote": "q1"},
          {"key": "cassini huygens", "name": "Cassini spacecraft", "wiki_title": "Cassini–Huygens", "section": 1, "quote": "q2"},
          {"key": "titan", "name": "Titan", "wiki_title": "Titan", "section": 1, "quote": "q3"},               # disambiguation
          {"key": "kraken sea", "name": "Kraken Sea", "wiki_title": "Kraken Sea", "section": 1, "quote": "q4"},  # missing
          {"key": "mystery", "name": "Mystery", "wiki_title": "Mystery", "section": 2, "quote": "q5"},          # no good hit
          {"key": "great white spot", "name": "Great White Spot", "wiki_title": "Great White Spot", "section": 2, "quote": "q6"},
          {"key": "ring", "name": "Encke Gap", "wiki_title": "Encke Gap", "section": 2, "quote": "q7"}]
    calls = []
    out = V.resolve_titles(ms, topic="Saturn", wiki_title="Saturn", transport=_wiki_transport(calls))
    got = {r["quote"]: (r["key"], r["wiki_title"]) for r in out}
    assert got["q1"] == got["q2"] == ("cassini huygens", "Cassini–Huygens")      # redirect variants merged
    assert got["q3"] == ("titan", "Titan (moon)")                                 # disambiguation → search
    assert got["q4"] == ("kraken mare", "Kraken Mare")                            # missing → search
    assert got["q5"] == ("mystery", "Mystery")                                    # nothing better: unchanged
    assert got["q6"] == ("great white spot", "Great White Spot")                  # section redirect keeps its own title
    assert got["q7"] == ("encke gap", "Encke Gap")
    assert sum(1 for c in calls if c.get("list") == "search") == 3 and len(calls) == 4
    assert ms[0]["key"] == "cassini"                                              # input not mutated
    calls.clear()
    assert V.resolve_titles(ms, topic="Saturn", transport=_wiki_transport(calls, fail=True)) == ms   # network down: unchanged


# ---------------------------------------------------------------- 4. rank_entities / gather_entity_photos / credits

def test_rank_entities_keeps_every_sections_top_things():
    ms = [{"key": "x", "name": "X", "section": 0, "pos": 0.1, "rank": 0, "kind": "place"} for _ in range(10)]
    ms += [{"key": f"w{i}", "name": f"W{i}", "section": 0, "pos": 0.2 + i / 10, "rank": i + 1} for i in range(5) for _ in range(3)]
    ms += [{"key": "y", "name": "Y", "wiki_title": "Y (thing)", "section": 1, "pos": 0.5, "rank": 0},
           {"key": "y", "name": "Y", "wiki_title": "Y", "section": 1, "pos": 0.6, "rank": 0},
           {"key": "z", "name": "Z", "section": 2, "pos": 0.5, "rank": 0}]
    ranked = V.rank_entities(ms, max_entities=4, per_section=1)
    assert [e["key"] for e in ranked] == ["x", "w0", "y", "z"]          # w1…w4 (3 mentions each) lose to y and z
    y = ranked[2]
    assert y["wiki_title"] == "Y (thing)" and y["mentions"] == 2 and y["sections"] == [1]
    assert ranked[0]["kind"] == "place" and V.rank_entities([]) == []
    assert [e["key"] for e in V.rank_entities(ms, max_entities=10)][:2] == ["x", "w0"]


def _jpeg_blob():
    from PIL import Image
    b = io.BytesIO()
    Image.new("RGB", (1920, 1080), (40, 60, 90)).save(b, "JPEG")
    return b.getvalue() + b"\0" * 30_000


BLOB = _jpeg_blob()


def _thumb(title):
    return f"https://up.test/{title}"


def _photo_transport(nasa_queries):
    files = {"Titan (moon)": ["File:Titan A.jpg", "File:Titan B.jpg", "File:Shared.jpg", "File:Titan map.jpg"],
             "Bayon": ["File:Shared.jpg", "File:Bayon face.jpg", "File:Bayon B.jpg", "File:Reel.jpg", "File:Bayon two.jpg"],
             "Boom": ["File:Boom 1.jpg"], "Unseen": ["File:Unseen.jpg"]}

    def ii(t):
        return {"title": t, "imageinfo": [{"mime": "image/jpeg", "width": 4000, "height": 2600, "thumburl": _thumb(t),
                "descriptionurl": f"https://commons.test/{t}", "extmetadata": {"LicenseShortName": {"value": "CC BY-SA 4.0"},
                "Artist": {"value": "Jane"}, "ImageDescription": {"value": "d"}}}]}

    def handler(req):
        host, p = req.url.host, req.url.params
        if host in ("up.test", "images-assets.nasa.gov"):
            return httpx.Response(200, content=BLOB + str(req.url).encode())
        if host == "en.wikipedia.org":
            if p.get("titles") == "Ghost thing":
                return httpx.Response(500)
            if p.get("prop") == "images":
                return httpx.Response(200, json={"query": {"pages": [{"title": p["titles"], "images": [
                    {"title": f} for f in files.get(p["titles"], [])]}]}})
            if p.get("prop") == "pageprops":
                pp = {"pageprops": {"wikibase_item": "Q2565"}} if p["titles"] == "Titan (moon)" else {}
                return httpx.Response(200, json={"query": {"pages": [{"title": p["titles"], **pp}]}})
        if host == "www.wikidata.org":
            return httpx.Response(200, json={"claims": {"P373": [{"mainsnak": {"datavalue": {"value": "Titan (moon)"}}}]}})
        if host == "commons.wikimedia.org":
            if p.get("titles"):
                return httpx.Response(200, json={"query": {"pages": [ii(t) for t in p["titles"].split("|")]}})
            if p.get("generator") == "categorymembers":
                return httpx.Response(200, json={"query": {"pages": [ii(f"File:Titan cat {i}.jpg") for i in range(2)]}})
            if p.get("list") == "categorymembers":
                return httpx.Response(200, json={"query": {"categorymembers": []}})
        if host == "images-api.nasa.gov":
            nasa_queries.append(p["q"])
            items = [{"data": [{"nasa_id": "PIA-T1", "title": "Titan haze", "description": "", "secondary_creator": "NASA/JPL"}]}]
            return httpx.Response(200, json={"collection": {"items": items if p.get("page") == "1" else []}})
        return httpx.Response(404)
    return httpx.MockTransport(handler)


def test_gather_entity_photos_dedupes_filters_and_never_raises(tmp_path):
    subjects, nasa_queries = [], []

    def classify(paths, subject):
        subjects.append(subject)
        if subject.startswith("Boom"):
            raise RuntimeError("vision API down")
        rows = {r["file"]: r for r in json.loads((Path(paths[0]).parent / "credits.json").read_text())}
        return {p: ("reject" if " B.jpg" in rows[Path(p).name]["url"] else "subject") for p in paths}

    reel = tmp_path / "reel.jpg"
    reel.write_bytes(BLOB + str(httpx.URL(_thumb("File:Bayon two.jpg"))).encode())    # same bytes as one Bayon download
    ents = [{"key": "titan", "name": "Titan", "wiki_title": "Titan (moon)", "kind": "celestial"},
            {"key": "bayon", "name": "Bayon", "wiki_title": "Bayon", "kind": "building"},
            {"key": "ghost", "name": "Ghost", "wiki_title": "Ghost thing", "kind": "other"},
            {"key": "bayon", "name": "Bayon again", "wiki_title": "Bayon", "kind": "building"},   # duplicate key: ignored
            {"key": "boom", "name": "Boom", "wiki_title": "Boom", "kind": "artefact"},
            {"key": "unseen", "name": "Unseen", "wiki_title": "Unseen", "kind": "place"}]          # beyond max_entities
    out_dir = tmp_path / "entities"
    got = V.gather_entity_photos(ents, str(out_dir), topic="Saturn", use_nasa=True, classify_fn=classify, per_entity=4,
                                 max_entities=4, exclude_urls={_thumb("File:Reel.jpg")}, exclude_paths=[str(reel)],
                                 transport=_photo_transport(nasa_queries), workers=2)
    assert list(got) == ["titan", "bayon", "ghost", "boom"]
    assert got["ghost"] == [] and got["boom"] == []                          # API error / vision error: no photos, no raise
    credits = V.credits_by_path(str(out_dir))
    urls = {k: [credits[p]["url"] for p in v] for k, v in got.items()}
    assert urls["titan"] == [_thumb("File:Titan A.jpg"), _thumb("File:Shared.jpg"),
                             "https://images-assets.nasa.gov/image/PIA-T1/PIA-T1~large.jpg", _thumb("File:Titan cat 0.jpg")]
    assert urls["bayon"] == [_thumb("File:Bayon face.jpg")]                  # Shared → Titan, Reel excluded, B rejected,
    assert all(Path(p).is_absolute() and Path(p).exists() for v in got.values() for p in v)   # "two" = reel file
    assert set(nasa_queries) == {"Titan"}                                    # NASA only for the celestial thing
    assert "Titan (Saturn)" in subjects and "Bayon (Saturn)" in subjects
    assert not any(u == _thumb("File:Shared.jpg") for p, r in credits.items() if "/bayon-" in p for u in [r["url"]])
    assert credits[got["titan"][0]]["license"] == "CC BY-SA 4.0" and credits[got["titan"][0]]["artist"] == "Jane"
    assert len({Path(p).parent for v in got.values() for p in v}) == 2       # one folder per thing


def test_gather_urls_passes_the_category_budget(monkeypatch):
    seen = []
    monkeypatch.setattr(images, "commons_category_images", lambda c, title, **kw: (seen.append(kw), [])[1])
    t = httpx.MockTransport(lambda r: httpx.Response(404))
    kw = dict(topic="Titan", wiki_title="Titan (moon)", terms=["titan"], queries=[], setting_queries=[], target=12,
              pexels_key="", pixabay_key="", transport=t)
    assert images.gather_urls(**kw) == [] and images.gather_urls(**kw, category_limit=100, category_subcats=2,
                                                                  category_per_subcat=30) == []
    assert seen == [{"seed": "Titan", "limit": 250, "subcats": 6, "per_subcat": 60},          # defaults unchanged
                    {"seed": "Titan", "limit": 100, "subcats": 2, "per_subcat": 30}]


def test_credits_by_path_reads_every_folder(tmp_path):
    (tmp_path / "a").mkdir()
    (tmp_path / "a" / "000.jpg").write_bytes(b"x")
    (tmp_path / "a" / "credits.json").write_text(json.dumps([{"file": "000.jpg", "url": "u-a"}, {"file": "001.jpg", "url": "gone"}]))
    (tmp_path / "000.jpg").write_bytes(b"y")
    (tmp_path / "credits.json").write_text(json.dumps([{"file": "000.jpg", "url": "u-root"}, "junk"]))
    (tmp_path / "b").mkdir()
    (tmp_path / "b" / "credits.json").write_text("{not json")
    got = V.credits_by_path(str(tmp_path))
    assert got == {str((tmp_path / "a" / "000.jpg").resolve()): {"file": "000.jpg", "url": "u-a"},
                   str((tmp_path / "000.jpg").resolve()): {"file": "000.jpg", "url": "u-root"}}


# ---------------------------------------------------------------- 5. plan_slots

REEL = [f"reel{i}.jpg" for i in range(10)]


def _m(key, t, name=None):
    return {"key": key, "name": name or key.title(), "time": t}


def _plan(n, mentions, photos, **kw):
    kw.setdefault("section_windows", [])
    kw.setdefault("fallback", REEL)
    return V.plan_slots(n, dwell=20.0, mentions=mentions, entity_photos=photos, **kw)


def test_named_thing_appears_in_its_slot_and_is_held_until_the_hold_expires():
    a = [f"a{i}.jpg" for i in range(8)]
    slots, plan = _plan(12, [_m("a", 47.0)], {"a": a})
    assert len(slots) == 12 and slots[0] == REEL[0] and plan[0]["why"].startswith("opening")
    # claimed slot = ceil((47 - 5) / 20) = 3, i.e. on screen at 60 s; held while start < 47 + 75
    assert [p["entity"] for p in plan] == [None, None, None, "a", "a", "a", "a", None, None, None, None, None]
    assert slots[3:7] == a[:4] and plan[3]["mention_time"] == 47.0 and plan[4]["mention_time"] is None
    assert "hold for 'A' expired" in plan[7]["why"]
    assert plan[3]["start"] - plan[3]["mention_time"] == 13.0


def test_lead_bounds_how_early_a_photo_may_appear():
    a = ["a0.jpg", "a1.jpg"]
    _, plan = _plan(6, [_m("a", 45.0)], {"a": a})
    assert plan[2]["entity"] == "a" and plan[2]["start"] == 40.0                  # 5 s early: allowed
    _, plan = _plan(6, [_m("a", 45.5)], {"a": a})
    assert plan[2]["entity"] is None and plan[3]["entity"] == "a"                 # 5.5 s early: next slot, 14.5 s late
    _, plan = _plan(6, [_m("a", 45.5)], {"a": a}, lead=10.0)
    assert plan[2]["entity"] == "a"                                               # the old "first half" rule


def test_photos_running_out_and_renewed_holds():
    _, plan = _plan(10, [_m("a", 47.0)], {"a": ["a0", "a1"]}, hold=1000)
    assert [p["entity"] for p in plan[2:7]] == [None, "a", "a", None, None] and "used up" in plan[5]["why"]
    a = [f"a{i}" for i in range(9)]
    _, plan = _plan(12, [_m("a", 47.0), _m("a", 100.0)], {"a": a})               # renewed: held while start < 175
    assert [p["entity"] for p in plan] == [None, None, None] + ["a"] * 6 + [None] * 3
    assert plan[5]["mention_time"] == 100.0 and "again" in plan[5]["why"]
    assert len({p["image"] for p in plan[3:9]}) == 6                              # a different photo every slot


def test_mention_without_photos_ends_the_run_and_latest_mention_wins():
    a, c = [f"a{i}" for i in range(6)], [f"c{i}" for i in range(6)]
    _, plan = _plan(10, [_m("a", 47.0), _m("b", 85.0, "Huygens probe")], {"a": a, "b": []})
    assert [p["entity"] for p in plan[2:7]] == [None, "a", None, None, None]
    assert "'Huygens probe' named at 85.0s has no photos" in plan[4]["why"]
    _, plan = _plan(10, [_m("a", 50.0), _m("c", 59.0)], {"a": a, "c": c})       # both claim slot 3
    assert plan[3]["entity"] == "c" and "1 earlier mention(s) in this slot skipped" in plan[3]["why"]
    assert all(p["entity"] != "a" for p in plan)


def test_opening_is_reserved_and_filler_prefers_the_section_pool():
    pool = ["p0", "p1", "p2"]
    slots, plan = _plan(12, [_m("a", 2.0)], {"a": ["a0"]}, section_windows=[(0, 100), (100, 240)], section_pools={1: pool})
    assert slots[0] == REEL[0] and all(p["entity"] is None for p in plan)        # a claim of slot 0 is dropped
    assert slots[1:5] == REEL[1:5]                                               # section 0 has no pool
    assert slots[5:8] == pool and plan[5]["why"] == "section photo" and plan[5]["section"] == 1
    assert slots[8:12] == REEL[5:9]                                              # pool used recently → reel


def test_repeats_are_spread_out_and_never_consecutive():
    slots, _ = _plan(40, [], {}, fallback=REEL)
    assert slots[:10] == REEL and slots[10:20] == REEL                           # rotation, best first
    uses = {}
    for k, s in enumerate(slots):
        uses.setdefault(s, []).append(k)
    assert min(b - a for v in uses.values() for a, b in zip(v, v[1:])) > 8
    slots, _ = _plan(10, [], {}, fallback=["r0", "r1", "r2"])                     # too few photos for the window
    assert all(a != b for a, b in zip(slots, slots[1:])) and slots[:6] == ["r0", "r1", "r2"] * 2
    assert _plan(4, [], {}, fallback=["only"])[0] == ["only"] * 4                # one photo: nothing else possible
    slots, plan = _plan(3, [], {"a": ["a0", "a1"]}, fallback=[])                 # no reel: any photo
    assert slots == ["a0", "a1", "a0"]
    with pytest.raises(ValueError):
        _plan(3, [], {}, fallback=[])
    assert _plan(0, [], {}) == ([], [])


def test_section_pools_and_windows_helpers():
    ms = [{"key": "b", "section": 0, "time": 5.0}, {"key": "a", "section": 0, "time": 9.0}, {"key": "b", "section": 1, "time": 30.0},
          {"key": "c", "section": 1, "time": 31.0}]
    pools = V.build_section_pools(ms, {"a": ["a0", "a1", "a2"], "b": ["b0"], "c": []})
    assert pools == {0: ["b0", "a0", "a1", "a2"], 1: ["b0"]}
    assert V.windows_from_starts([0.0, 30.0, 30.0, 70.0], 100.0) == [(0.0, 30.0), (30.0, 30.0), (30.0, 70.0), (70.0, 100.0)]
    assert V._section_at([(0.0, 30.0), (30.0, 30.0), (30.0, 70.0)], 30.0) == 2 and V._section_at([(0.0, 30.0)], 99.0) == 0


def test_filler_never_spoils_and_holds_end_with_the_section():
    a, b = [f"a{i}" for i in range(6)], [f"b{i}" for i in range(6)]
    windows = [(0.0, 100.0), (100.0, 300.0)]
    slots, plan = _plan(12, [_m("a", 60.0), _m("b", 150.0)], {"a": a, "b": b}, hold=1000.0, section_windows=windows,
                        section_pools={0: a + b, 1: b})
    assert slots[1:3] == REEL[1:3]                                   # A is not named before 60 s: no A photo yet
    assert [p["entity"] for p in plan[3:5]] == ["a", "a"]            # named at 60 → slot 3, held through slot 4
    assert plan[5]["entity"] is None and "new section; 'A' released" in plan[5]["why"]
    assert slots[5:8] == REEL[3:6]                                   # B's photos wait until B is named at 150 s
    assert plan[8]["entity"] == "b" and plan[8]["mention_time"] == 150.0


def _check_invariants(slots, plan, *, n, dwell, lead, hold, mentions, photos, fallback, windows=()):
    everything = set(fallback) | {p for v in photos.values() for p in v}
    assert len(slots) == len(plan) == n
    if n:
        assert slots[0] == (fallback[0] if fallback else slots[0])
    for k, p in enumerate(plan):
        assert p["slot"] == k and p["image"] == slots[k] and abs(p["start"] - k * dwell) < 1e-6
        assert slots[k] in everything
        if p["entity"]:
            assert slots[k] in photos[p["entity"]]
    if len(everything) > 1:
        assert all(a != b for a, b in zip(slots, slots[1:]))
    # a run of one thing never shows a photo twice
    k = 0
    while k < n:
        j = k
        while j + 1 < n and plan[j + 1]["entity"] and plan[j + 1]["entity"] == plan[k]["entity"]:
            j += 1
        if plan[k]["entity"]:
            assert len(set(slots[k:j + 1])) == j + 1 - k
        k = j + 1
    # the latest mention claiming a slot is on screen there whenever it can be (new thing, a photo ≠ the previous one)
    claims = {}
    for m in sorted(mentions, key=lambda m: m["time"]):
        c = max(0, math.ceil((m["time"] - lead) / dwell - 1e-9))
        if 1 <= c < n:
            claims.setdefault(c, []).append(m)
    for c, ms in claims.items():
        m = ms[-1]
        if m["key"] != plan[c - 1]["entity"] and any(p != slots[c - 1] for p in photos.get(m["key"], [])):
            assert plan[c]["entity"] == m["key"] and plan[c]["mention_time"] == m["time"]
    # a held slot is within the hold of the thing's latest mention so far, and in the same section
    windows = list(windows)
    for k, p in enumerate(plan):
        if p["entity"] and p["mention_time"] is None:
            last = max(m["time"] for m in mentions if m["key"] == p["entity"]
                       and max(0, math.ceil((m["time"] - lead) / dwell - 1e-9)) <= k)
            assert k * dwell < last + hold
            assert V._section_at(windows, (k + 0.5) * dwell) == V._section_at(windows, last)
    # a section photo of a thing is only shown once the thing was named in that section
    for k, p in enumerate(plan):
        owners = [key for key, v in photos.items() if slots[k] in v]
        if p["why"].startswith("section photo") and owners:
            lo = windows[p["section"]][0] if windows else -math.inf
            assert any(lo <= m["time"] <= k * dwell + lead for m in mentions if m["key"] in owners)


def test_plan_invariants_hold_for_random_inputs():
    for seed in range(300):
        rnd = random.Random(seed)
        n = rnd.randint(0, 60)
        dwell = rnd.choice([10.0, 20.0])
        lead, hold, avoid = rnd.choice([0.0, 2.0, 5.0, 10.0]), rnd.choice([20.0, 75.0, 300.0]), rnd.choice([0, 3, 8])
        photos = {f"e{i}": [f"e{i}_{j}" for j in range(rnd.randint(0, 5))] for i in range(rnd.randint(0, 6))}
        if rnd.random() < 0.3 and photos:                                    # photos shared between things and the reel
            photos[next(iter(photos))].append("r0")
        fallback = [f"r{j}" for j in range(rnd.choice([0, 1, 2, 5, 12]))]
        if not fallback and not any(photos.values()):
            fallback = ["r0"]
        keys = list(photos) + ["nophotos"]
        mentions = [{"key": rnd.choice(keys), "name": "x", "time": round(rnd.uniform(0, n * dwell + 30), 1)}
                    for _ in range(rnd.randint(0, 3 * n + 1))]
        windows = [(0.0, n * dwell / 2), (n * dwell / 2, n * dwell)]
        pools = {1: [p for v in photos.values() for p in v][:4]} if rnd.random() < 0.5 else None
        kw = dict(dwell=dwell, mentions=mentions, entity_photos=photos, section_windows=windows, fallback=fallback,
                  section_pools=pools, lead=lead, hold=hold, avoid_repeat_within=avoid)
        slots, plan = V.plan_slots(n, **kw)
        assert (slots, plan) == V.plan_slots(n, **kw)                         # deterministic
        _check_invariants(slots, plan, n=n, dwell=dwell, lead=lead, hold=hold, mentions=mentions, photos=photos,
                          fallback=fallback, windows=windows)


# ---------------------------------------------------------------- 6. end to end (no network)

def test_end_to_end_saturn_plan_follows_the_narration():
    chunks, owner, durs = _chunked(limit=300, cps=8.0)                       # slow narration: ~6 min for 3 sections
    mentions = V.extract_mentions(FakeLLM(CANNED), SECTIONS, topic="Saturn", model="cheap")
    timed = V.mention_times(mentions, chunks=chunks, owner=owner, chunk_durations=durs)
    ranked = V.rank_entities(timed, max_entities=8, per_section=2)
    assert len(ranked) == 8 and {e["key"] for e in ranked} >= {"cassini division", "titan", "enceladus"}
    photos = {e["key"]: [f"/w/entities/{e['key']}/{j:03d}.jpg" for j in range(3 if e["key"] != "huygens" else 0)]
              for e in ranked}
    starts = tts.section_starts([str(i) for i in range(len(chunks))], owner, len(SECTIONS), duration=lambda p: durs[int(p)])
    total = sum(durs)
    windows = V.windows_from_starts(starts, total)
    reel = [f"/w/images/{i:03d}.jpg" for i in range(12)]
    n = math.ceil(total / 20.0)
    slots, plan = V.plan_slots(n, dwell=20.0, mentions=timed, entity_photos=photos, section_windows=windows,
                               fallback=reel, section_pools=V.build_section_pools(timed, photos))
    _check_invariants(slots, plan, n=n, dwell=20.0, lead=5.0, hold=75.0, mentions=timed, photos=photos, fallback=reel,
                      windows=windows)
    assert slots[:2] == reel[:2]                     # nothing named yet: the subject itself, no spoiler of what is coming
    for m in timed:
        a, b = windows[m["section"]]
        assert a <= m["time"] < b and m["timing"] == "quote"
    # the first time each thing with photos is named, it is on screen within one slot, unless a later
    # mention took that slot (then the plan says so)
    first = {}
    for m in timed:
        first.setdefault(m["key"], m)
    for key, m in first.items():
        if not photos.get(key):
            continue
        c = math.ceil((m["time"] - 5.0) / 20.0 - 1e-9)
        shown = [p for p in plan[c:c + 2] if p["entity"] == key]
        assert shown or "skipped" in plan[c]["why"], (key, m["time"], plan[c])
    huygens = next(m for m in timed if m["key"] == "huygens")
    hc = math.ceil((huygens["time"] - 5.0) / 20.0 - 1e-9)
    assert plan[hc]["entity"] is None and "has no photos" in plan[hc]["why"]
    stats = V.plan_stats(plan)
    assert stats["slots"] == n and stats["consecutive_repeats"] == 0 and stats["thing_slots"] >= n // 3
    assert -5.0 <= stats["lag_min"] and stats["lag_max"] <= 15.0 and json.dumps(plan)   # serialisable for timeline.json


def test_gather_entity_photos_respects_its_time_budget(tmp_path):
    calls = []

    def classify(paths, subject):
        calls.append(subject)
        return {p: "subject" for p in paths}

    ents = [{"key": f"thing{i}", "name": f"Thing {i}", "wiki_title": f"Thing {i}", "kind": "place"} for i in range(3)]
    ticks = iter([0.0, 1000.0] + [1000.0] * 100)         # budget already spent after the start
    got = V.gather_entity_photos(ents, str(tmp_path), topic="T", use_nasa=False, classify_fn=classify,
                                       budget_seconds=60, clock=lambda: next(ticks))
    assert got == {"thing0": [], "thing1": [], "thing2": []} and calls == []


def test_filler_recalls_only_recently_named_things():
    # one section of 600 s; "lighthouse" named at 30 s with 2 photos; nothing else is named
    photos = {"lighthouse": ["L1", "L2"]}
    mentions = [{"key": "lighthouse", "name": "Lighthouse", "time": 30.0, "section": 0}]
    slots, plan = V.plan_slots(30, dwell=20, mentions=mentions, entity_photos=photos, section_windows=[(0.0, 600.0)],
                               fallback=["R0", "R1", "R2", "R3", "R4", "R5"], section_pools={0: ["L1", "L2"]},
                               hold=40, recall=150, avoid_repeat_within=2)
    late = [p for k, p in enumerate(slots) if k * 20 > 30 + 150 + 5]
    assert late and not set(late) & {"L1", "L2"}                  # long after the mention: neutral subject photos
    assert set(slots[1:10]) & {"L1", "L2"}                         # while it is fresh: the thing itself
