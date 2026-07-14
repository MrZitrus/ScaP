import json

from config_manager import ConfigManager


def test_logging_does_not_replace_active_api_keys(tmp_path) -> None:
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps({
            "real_debrid": {"api_key": "rd-secret"},
            "jellyfin": {"api_key": "jellyfin-secret"},
            "gemini": {"api_key": "gemini-secret"},
        }),
        encoding="utf-8",
    )

    config = ConfigManager(config_file=str(config_path), env_file=str(tmp_path / ".env"))

    assert config.get("real_debrid.api_key") == "rd-secret"
    assert config.get("jellyfin.api_key") == "jellyfin-secret"
    assert config.get("gemini.api_key") == "gemini-secret"
