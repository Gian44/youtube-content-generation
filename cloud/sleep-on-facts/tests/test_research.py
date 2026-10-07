import httpx

from sof import research

ARTICLE = ("Whales are marine mammals of the order Cetacea. " * 12 + "\n\n== Anatomy ==\n" +
           "Baleen plates filter krill from seawater, and a blue whale heart weighs about 180 kilograms. " * 10 +
           "\n\n== Song ==\n" + "Humpback whale song is produced by males and can last twenty minutes. Songs change over years. " * 10 +
           "\n\n== See also ==\nList of cetaceans\n\n== References ==\nfoo")
LINKED = {"Humpback whale": "Humpback whales sing complex songs. " * 400, "Krill": "Krill are small crustaceans eaten by whales. " * 400,
          "Stub": "Too short."}


def _transport():
    def handler(req):
        q = dict(req.url.params)
        if q.get("list") == "search":
            return httpx.Response(200, json={"query": {"search": [{"title": "Whale"}]}})
        if q.get("prop") == "extracts":
            t = q["titles"]
            text = ARTICLE if t == "Whale" else LINKED.get(t, "")
            return httpx.Response(200, json={"query": {"pages": {"1": {"title": t, "extract": text}}}})
        if q.get("prop") == "links":
            return httpx.Response(200, json={"query": {"pages": {"1": {"links": [{"title": "Humpback whale"}, {"title": "Krill"},
                                                                                {"title": "Stub"}, {"title": "Unrelated"}]}}}})
        if q.get("prop") == "info":
            return httpx.Response(200, json={"query": {"pages": {"1": {"title": q["titles"], "length": 90000 if q["titles"] == "Whale" else 500}}}})
        if "titles" in q:   # resolve_title
            if q["titles"].lower().startswith("whale"):
                return httpx.Response(200, json={"query": {"pages": {"1": {"title": "Whale"}}}})
            return httpx.Response(200, json={"query": {"pages": {"-1": {"title": q["titles"], "missing": ""}}}})
        return httpx.Response(404)
    return httpx.MockTransport(handler)


def test_split_sections_drops_reference_sections():
    lead, sections = research.split_sections(ARTICLE)
    assert lead.startswith("Whales are marine mammals") and [h for h, _ in sections] == ["Anatomy", "Song"]


def test_build_corpus_pulls_linked_articles_ranked_by_mentions(monkeypatch):
    import tests.test_research as me
    monkeypatch.setattr(me, "ARTICLE", ARTICLE + " Humpback whale humpback whale krill krill krill.")   # mentions make them rank
    c = research.build_corpus("whales", transport=_transport(), max_linked=5, min_linked_chars=1000)
    assert c.title == "Whale" and c.url.endswith("/wiki/Whale") and c.sections == ["Anatomy", "Song"]
    assert c.sources[0] == "Whale" and set(c.sources[1:]) == {"Humpback whale", "Krill"}   # Stub too short, Unrelated unmentioned
    assert any(ch.source == "Whale — Song" for ch in c.chunks) and c.chars > 5000
    assert research.Corpus.from_dict(c.to_dict()).chars == c.chars


def test_retrieve_matches_the_right_section():
    c = research.build_corpus("Whale", transport=_transport(), max_linked=0)
    top = research.retrieve(c, "song: how males sing, songs change over the years", k=2)
    assert top and top[0].source == "Whale — Song"
    assert "[Whale — Song]" in research.notes_block(top)


def test_chunk_text_respects_size():
    chunks = research.chunk_text("S", "para one is here and long enough. " * 30 + "\n\n" + "para two is also long enough. " * 30, size=400)
    assert all(len(ch.text) <= 400 for ch in chunks) and len(chunks) >= 4


def test_hints_degrade_to_empty_on_failure():
    assert research.wikipedia_hints("X", transport=httpx.MockTransport(lambda r: httpx.Response(403))) == ""
    assert research.wikipedia_hints("Whale", transport=_transport()).startswith("Whales are marine mammals")


def test_linked_articles_need_reciprocal_mention_and_skip_generic(monkeypatch):
    import tests.test_research as me
    monkeypatch.setattr(me, "ARTICLE", ARTICLE + " Humpback whale humpback whale krill krill krill world world world region region.")
    monkeypatch.setitem(LINKED, "World", "The world is large. " * 500)            # generic → skipped by name
    monkeypatch.setitem(LINKED, "Region", "A region is an area. " * 500)         # never mentions whales → skipped
    monkeypatch.setitem(LINKED, "Krill", "Krill are eaten by baleen whales. " * 400)

    def handler(req):
        q = dict(req.url.params)
        if q.get("prop") == "links":
            return httpx.Response(200, json={"query": {"pages": {"1": {"links": [{"title": t} for t in ("Humpback whale", "Krill", "World", "Region", "Stub")]}}}})
        return _transport().handler(req)
    c = research.build_corpus("Whale", transport=httpx.MockTransport(handler), max_linked=5, min_linked_chars=1000)
    assert set(c.sources) == {"Whale", "Humpback whale", "Krill"}
