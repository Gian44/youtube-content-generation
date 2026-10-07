import httpx

from sof import topicgen


class FakeLLM:
    def __init__(self):
        self.calls = []

    def generate_json(self, prompt, **kw):
        self.calls.append((prompt, kw.get("model")))
        if "Rejected this round" in prompt and "Tiny Thing" in prompt:   # second round after a rejection
            return {"candidates": [{"topic": "Honeybees", "wikipedia_title": "Honey bee"}]}
        return {"candidates": [{"topic": "Ancient Egypt", "wikipedia_title": "Ancient Egypt"},   # used → skipped
                               {"topic": "Tiny Thing", "wikipedia_title": "Tiny thing"},         # thin → rejected
                               {"topic": "Honeybees", "wikipedia_title": "Honey bee"}]}


def _transport():
    def handler(req):
        q = dict(req.url.params)
        if q.get("prop") == "info":
            return httpx.Response(200, json={"query": {"pages": {"1": {"title": q["titles"], "length": 4000 if "Tiny" in q["titles"] else 150000}}}})
        if "titles" in q:
            return httpx.Response(200, json={"query": {"pages": {"1": {"title": q["titles"]}}}})
        return httpx.Response(404)
    return httpx.MockTransport(handler)


def test_next_category_rotates_by_fewest_videos():
    cats = ["nature", "history", "space"]
    assert topicgen.next_category(cats, []) == "nature"
    assert topicgen.next_category(cats, [{"category": "nature"}]) == "history"
    assert topicgen.next_category(cats, [{"category": "nature"}, {"category": "history"}, {"category": "space"}]) == "nature"


def test_choose_skips_used_and_thin_topics():
    llm = FakeLLM()
    pick = topicgen.choose(llm, categories=["nature"], ledger=[{"topic": "Ancient Egypt", "wiki_title": "Ancient Egypt", "category": "history"}],
                           model="fast", transport=_transport())
    assert pick.topic == "Honeybees" and pick.wiki_title == "Honey bee" and pick.category == "nature"
    assert llm.calls[0][1] == "fast" and "Ancient Egypt" in llm.calls[0][0]
