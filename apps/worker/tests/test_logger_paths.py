from pathlib import Path

from storyfactory.logger import setup_logging


def test_setup_logging_uses_storyfactory_log_dir(tmp_path, monkeypatch):
    log_dir = tmp_path / "desktop-logs"
    monkeypatch.setenv("STORYFACTORY_LOG_DIR", str(log_dir))

    setup_logging()

    assert log_dir.exists()
    assert log_dir.is_dir()
    assert not Path("logs").resolve().samefile(log_dir) if Path("logs").exists() else True
