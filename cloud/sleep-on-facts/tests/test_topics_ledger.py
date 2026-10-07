import json
from sof.topics import load_topics, load_categories, unused_seed, pick_next, Topic
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
    assert load_topics("topics.yml") == []          # generated daily; see topics.yml
    assert len(load_categories("topics.yml")) == 8


def test_unused_seed_then_none(tmp_path):
    p = tmp_path / "topics.yml"; p.write_text(YAML)
    topics = load_topics(str(p))
    assert unused_seed(topics, []).name == "Ancient Egypt"
    assert unused_seed(topics, [{"topic": "ancient egypt"}]).name == "The Deep Sea"
    assert unused_seed(topics, [{"topic": "Ancient Egypt"}, {"topic": "The Deep Sea"}]) is None


def test_ledger_roundtrip(tmp_path):
    p = tmp_path / "ledger.json"
    assert load_ledger(str(p)) == []
    e = append_entry(str(p), {"date": "2026-10-07", "topic": "Whales", "video_id": "abc"})
    assert load_ledger(str(p)) == [e] and json.loads(p.read_text())[0]["video_id"] == "abc"
    assert commit_message(e) == "ledger: 2026-10-07 Whales (abc)"


def test_ledger_push_reapplies_entry_after_conflict(tmp_path, monkeypatch):
    from sof import ledger as L
    p = tmp_path / "ledger.json"
    p.write_text(json.dumps([{"date": "2026-10-07", "topic": "Egypt", "video_id": "a"}]))
    entry = {"date": "2026-10-08", "topic": "Honeybees", "video_id": "b"}
    L.append_entry(str(p), entry)
    calls = []

    class R:  # fake CompletedProcess
        def __init__(self, rc): self.returncode = rc

    def fake_git(*args, check=True):
        calls.append(args)
        if args[0] == "pull" and calls.count(args) == 1:
            return R(1)                    # first rebase conflicts
        if args[:2] == ("reset", "--hard"):
            # remote ledger has a different newer row and lacks ours
            p.write_text(json.dumps([{"date": "2026-10-07", "topic": "Egypt", "video_id": "a"},
                                     {"date": "2026-10-08", "topic": "Other", "video_id": "z"}]))
        return R(0)

    L.git_commit_and_push(str(p), entry, run=fake_git)
    rows = json.loads(p.read_text())
    assert [r["video_id"] for r in rows] == ["a", "z", "b"]   # remote kept, ours re-appended
    assert ("push",) in calls and sum(1 for c in calls if c[0] == "commit") == 2
