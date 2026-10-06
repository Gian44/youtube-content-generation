import httpx
from sof.research import wikipedia_hints


def test_hints_from_extract():
    t = httpx.MockTransport(lambda r: httpx.Response(200, json={"query": {"pages": {"1": {"extract": "Ancient Egypt was a civilization. " * 200}}}}))
    hints = wikipedia_hints("Ancient Egypt", transport=t)
    assert hints.startswith("Ancient Egypt was a civilization.") and len(hints) <= 4000


def test_failure_degrades_to_empty():
    assert wikipedia_hints("X", transport=httpx.MockTransport(lambda r: httpx.Response(403))) == ""
