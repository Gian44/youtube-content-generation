from datetime import datetime, timezone
from pathlib import Path

import pytest

from cny import films, plot


def _row(film, year, kind="video", **kw):
    return {"kind": kind, "film": film, "year": year, "video_id": "v" if kind == "video" else None, **kw}


def test_load_films_keeps_theme_order(tmp_path):
    p = tmp_path / "films.yml"
    p.write_text("themes:\n  - name: A\n    films:\n      - {title: One, year: 2001}\n      - Two\n  - name: B\n    films:\n      - {title: Three, year: 2003}\n",
                 encoding="utf-8")
    fs = films.load_films(str(p))
    assert [f.title for f in fs] == ["One", "Two", "Three"]
    assert fs[1].year is None and fs[1].theme == "A" and fs[2].theme == "B"
    assert fs[0].slug == "one-2001" and fs[1].slug == "two" and fs[0].label == "One (2001)"


def test_next_films_skips_published_and_skipped_until_plot_exists():
    fs = [films.Film("One", 2001, "A"), films.Film("Two", 2002, "A"), films.Film("Three", 2003, "A")]
    rows = [_row("One", 2001), _row("Two", 2002, kind="skipped")]
    assert [f.title for f in films.next_films(fs, rows, limit=3)] == ["Three"]
    assert [f.title for f in films.next_films(fs, rows, limit=3, retry_skipped={"two-2002"})] == ["Two", "Three"]
    assert [f.title for f in films.next_films(fs, [], limit=2)] == ["One", "Two"]


def test_find_film_prefers_the_yaml_row():
    fs = [films.Film("Memento", 2000, "Nolan")]
    assert films.find_film(fs, "memento").theme == "Nolan"
    assert films.find_film(fs, "Heat", 1995) == films.Film("Heat", 1995, "")


def test_ran_recently_uses_the_last_video_row():
    now = datetime(2026, 11, 8, 12, 0, tzinfo=timezone.utc)
    rows = [_row("One", 2001, uploaded_at="2026-11-07T10:00:00+00:00"), _row("Two", 2002, kind="skipped", date="2026-11-08")]
    assert "26 h ago" in films.ran_recently(rows, gap_hours=36, now=now)
    assert films.ran_recently(rows, gap_hours=20, now=now) is None
    assert films.ran_recently([], gap_hours=36, now=now) is None
    assert films.ran_recently([_row("X", 1, date="2026-11-08")], gap_hours=36, now=now)      # date-only rows count too


def test_daily_one_recap_per_utc_day_from_nine():
    from cny import daily
    up = _row("One", 2001, uploaded_at="2026-11-12T17:30:00+00:00")
    gone = dict(_row("Old", 1999, uploaded_at="2026-11-13T10:00:00+00:00"), taken_down=True)
    skipped = _row("Two", 2002, kind="skipped", date="2026-11-13")
    at = lambda h, d=13: datetime(2026, 11, d, h, 5, tzinfo=timezone.utc)  # noqa: E731
    assert daily.hold([up], now=at(18, 12)) and "already up" in daily.posted_today([up], now=at(23, 12))
    assert "before 09:00" in daily.hold([up], now=at(3))                     # last night's late trigger lands after midnight
    assert daily.hold([up], now=at(3), scheduled=False) is None              # a manual run is not held by the hour
    assert daily.hold([up, gone, skipped], now=at(16)) is None               # taken down / skipped rows are not today's recap
    assert daily.hold([dict(up, uploaded_at="2026-11-13T09:40:00Z")], now=at(22)) is not None
    assert daily.hold([dict(up, uploaded_at="bad")], now=at(22)) is None
    late = dict(up, date="2026-11-12", uploaded_at="2026-11-13T00:40:00+00:00")   # started 23:10 on the 12th, up after midnight
    assert daily.hold([late], now=at(10)) is None and daily.hold([late], now=at(23, 12))
    assert daily.hold([], now=at(9)) is None and daily.load_rows("/nonexistent/ledger.json") == []


def test_gate_script_runs_without_dependencies(tmp_path):
    import os
    import subprocess
    import sys
    here = Path(__file__).resolve().parents[1]
    out = tmp_path / "out.txt"
    env = dict(os.environ, GITHUB_EVENT_NAME="workflow_dispatch", GITHUB_OUTPUT=str(out))
    subprocess.run([sys.executable, "-I", "-S", str(here / "gate.py")], env=env, check=True, capture_output=True)  # -S: no site-packages
    assert out.read_text().strip() == "go=true"


def test_strip_wikitext_removes_refs_templates_links():
    src = ("In 2010, [[Dom Cobb|Cobb]] steals.<ref>cite</ref> {{citation needed}} ''Dreams'' end.\n\n"
           "[[File:x.jpg|thumb|caption]]The [https://x.org end] comes.")
    out = plot.strip_wikitext(src)
    assert out == "In 2010, Cobb steals. Dreams end.\n\nThe end comes."


def test_candidate_titles_and_trim():
    assert plot.candidate_titles("Heat", 1995) == ["Heat (1995 film)", "Heat (film)", "Heat"]
    assert plot.candidate_titles("Heat", None) == ["Heat (film)", "Heat"]
    text = "\n\n".join(f"para {i} " + "x" * 100 for i in range(10))
    kept = plot.trim_plot(text, 350)
    assert kept.endswith("para 9 " + "x" * 100) and kept.startswith("para 7")
    assert plot.trim_plot("short", 100) == "short"


def test_operator_plot_reads_lead_and_body(tmp_path):
    (tmp_path / "heat-1995.md").write_text("Lead: Michael Mann, 1995\n# Heat\n\nA thief and a cop.\n\nThey meet.", encoding="utf-8")
    p = plot.operator_plot(str(tmp_path), "heat-1995", title="Heat")
    assert p.section == "operator" and p.source == "operator" and p.lead == "Michael Mann, 1995"
    assert p.text == "A thief and a cop.\n\nThey meet." and p.word_count == 7
    assert plot.operator_plot(str(tmp_path), "nope", title="x") is None


def test_wikiplot_parses_sections_and_lead(httpx_mock_transport):
    wp = plot.WikiPlot(transport=httpx_mock_transport)
    got = wp.fetch("Heat (1995 film)")
    assert got.section == "Plot" and got.word_count == 14 and got.url.endswith("Heat_(1995_film)#Plot")
    assert got.lead.startswith("Heat is a 1995 American")
    assert wp.fetch("Missing") is None


@pytest.fixture
def httpx_mock_transport():
    import httpx

    def handler(request: httpx.Request) -> httpx.Response:
        q = dict(request.url.params)
        if q.get("page") == "Missing":
            return httpx.Response(200, json={"error": {"code": "missingtitle"}})
        if q.get("prop") == "sections":
            return httpx.Response(200, json={"parse": {"title": "Heat (1995 film)", "sections": [
                {"line": "Cast", "level": "2", "index": "1"}, {"line": "Plot", "level": "2", "index": "2"}]}})
        if q.get("section") == "0":
            return httpx.Response(200, json={"parse": {"wikitext": {"*": "{{Infobox}}\n'''Heat''' is a 1995 American crime film written and directed by [[Michael Mann]] about a thief and the detective who hunts him across Los Angeles."}}})
        return httpx.Response(200, json={"parse": {"wikitext": {"*": "==Plot==\nNeil McCauley robs an armoured car.<ref>x</ref> Vincent Hanna investigates and the two men meet."}}})

    return httpx.MockTransport(handler)
