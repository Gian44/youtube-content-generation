from sof.config import Config, parse_args


def test_defaults_and_env(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("PEXELS_API_KEY", "px")
    cfg = Config.from_env(minutes=180)
    # Gemini's free day voices ~130 min (16 movements + closing + Shorts in 20 requests)
    assert cfg.minutes == 130 and cfg.target_words == 130 * 140 and cfg.tts_backend == "gemini" and cfg.tts_chunk_chars == 8000
    assert Config.from_env(minutes=5).minutes == 5 and Config.from_env(minutes=120).minutes == 120
    monkeypatch.setenv("TTS_BACKEND", "pocket")
    assert Config.from_env(minutes=180).minutes == 180
    monkeypatch.delenv("TTS_BACKEND")
    assert Config(minutes=180, tts_backend="openai").target_words == 27000
    assert cfg.tts_model == "gpt-4o-mini-tts" and cfg.tts_instructions and Config().tts_chunk_chars <= 2000
    assert cfg.script_model == "gpt-5.6-terra" and cfg.fast_model == "gpt-5.6-luna"
    assert cfg.gemini_models == ["gemini-3.6-flash", "gemini-3.5-flash", "gemini-3.5-flash-lite"]
    assert cfg.images_target == 150 and cfg.images_min == 100
    assert cfg.privacy == "public" and not cfg.is_smoke
    assert cfg.openai_api_key == "sk-test" and cfg.pexels_api_key == "px"


def test_smoke_mode_forces_unlisted(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    cfg = Config.from_env(minutes=3, privacy="public")
    assert cfg.privacy == "unlisted" and cfg.is_smoke


def test_parse_args():
    ns = parse_args(["--minutes", "3", "--topic", "Whales", "--skip-upload"])
    assert ns.minutes == 3 and ns.topic == "Whales" and ns.skip_upload is True and ns.privacy is None
