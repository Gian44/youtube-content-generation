import json
from sof.topics import load_topics, pick_next, Topic
from sof.ledger import load_ledger, append_entry, commit_message

YAML = """
topics:
  - name: Ancient Egypt
    queries: [egypt pyramids desert, nile river sunset]
  - name: The Deep Sea
    queries: [deep ocean dark water]
"""


def test_load_and_pick_round_robin(tmp_path):
    p = tmp_path / "topics.yml"
    p.write_text(YAML)
    topics = load_topics(str(p))
    assert topics[0] == Topic(name="Ancient Egypt", queries=["egypt pyramids desert", "nile river sunset"])
    assert pick_next(topics, []).name == "Ancient Egypt"
    assert pick_next(topics, [{"topic": "Ancient Egypt"}]).name == "The Deep Sea"
    assert pick_next(topics, [{"topic": "Ancient Egypt"}, {"topic": "The Deep Sea"}]).name == "Ancient Egypt"
    assert pick_next(topics, [], forced="the deep sea").name == "The Deep Sea"
    assert pick_next(topics, [], forced="Owls").queries == ["Owls"]


def test_repo_topics_file_is_valid():
    topics = load_topics("topics.yml")
    assert len(topics) == 10 and all(len(t.queries) == 3 for t in topics)


def test_ledger_roundtrip(tmp_path):
    p = tmp_path / "ledger.json"
    assert load_ledger(str(p)) == []
    e = append_entry(str(p), {"date": "2026-10-07", "topic": "Whales", "video_id": "abc"})
    assert load_ledger(str(p)) == [e] and json.loads(p.read_text())[0]["video_id"] == "abc"
    assert commit_message(e) == "ledger: 2026-10-07 Whales (abc)"
