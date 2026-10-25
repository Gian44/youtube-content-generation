import json
import subprocess

from sof.llm import LLM


def _ok(result):
    return subprocess.CompletedProcess([], 0, stdout=json.dumps({"type": "result", "is_error": False, "result": result}), stderr="")


def test_claude_first_with_tier_by_model_and_no_tools():
    seen = []

    def runner(cmd, prompt, timeout):
        seen.append((cmd, prompt))
        return _ok('{"title": "T"}')
    llm = LLM("", "", "gpt-writer", [], claude=True, claude_writer="sonnet", claude_fast="haiku", claude_runner=runner)
    assert llm.generate_json("write it") == {"title": "T"}
    assert llm.generate_json("plan it", model="gpt-fast") == {"title": "T"}
    assert llm.generate_json("write more", model="gpt-writer") == {"title": "T"}
    models = [c[c.index("--model") + 1] for c, _ in seen]
    assert models == ["sonnet", "haiku", "sonnet"]
    cmd, prompt = seen[0]
    assert cmd[:2] == ["claude", "-p"] and cmd[cmd.index("--tools") + 1] == "" and "--no-session-persistence" in cmd
    assert prompt.startswith("write it") and llm.stats == {"claude": 3, "openai": 0, "gemini": 0}


def test_code_fences_and_a_stray_sentence_are_tolerated_then_one_retry():
    answers = iter([_ok("Sure! Here it is"), _ok('```json\n{"a": 1}\n```')])
    llm = LLM("", "", "w", [], claude=True, claude_runner=lambda c, p, t: next(answers))
    assert llm.generate_json("x") == {"a": 1}


def test_usage_limit_switches_claude_off_for_the_run_and_falls_back(monkeypatch):
    calls = []

    def runner(cmd, prompt, timeout):
        calls.append(1)
        return subprocess.CompletedProcess([], 1, stdout=json.dumps({"is_error": True, "result": "Claude AI usage limit reached|1761400000"}),
                                           stderr="")
    llm = LLM("", "gem-key", "w", ["gemini-x"], claude=True, claude_runner=runner)
    monkeypatch.setattr(llm, "_gemini", lambda *a, **k: '{"ok": true}')
    assert llm.generate_json("x") == {"ok": True}
    assert llm.generate_json("y") == {"ok": True}
    assert len(calls) == 1 and "usage limit" in llm._claude_off and llm.stats["gemini"] == 2


def test_claude_off_without_token_or_cli():
    llm = LLM("", "", "w", [], claude=False)
    assert llm._claude_off == "disabled"


def test_preflight_switches_claude_off_when_it_does_not_answer():
    def hang(cmd, prompt, timeout):
        raise subprocess.TimeoutExpired(cmd, timeout)
    llm = LLM("", "", "w", [], claude=True, claude_runner=hang)
    assert "preflight failed" in llm.check_claude(timeout=1) and llm._claude_off
    good = LLM("", "", "w", [], claude=True, claude_runner=lambda c, p, t: _ok('{"ok": true}'))
    assert good.check_claude() is None and good._claude_off is None
