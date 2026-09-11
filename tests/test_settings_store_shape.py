from src import settings


def test_load_settings_falls_back_for_non_object_json(tmp_path, monkeypatch):
    settings_file = tmp_path / "settings.json"
    settings_file.write_text("[]", encoding="utf-8")
    monkeypatch.setattr(settings, "SETTINGS_FILE", str(settings_file))
    settings._invalidate_caches()

    assert settings.load_settings() == settings.DEFAULT_SETTINGS
    assert settings.is_setting_overridden("default_model") is False


def test_load_features_falls_back_for_non_object_json(tmp_path, monkeypatch):
    features_file = tmp_path / "features.json"
    features_file.write_text("[]", encoding="utf-8")
    monkeypatch.setattr(settings, "FEATURES_FILE", str(features_file))
    settings._invalidate_caches()

    assert settings.load_features() == settings.DEFAULT_FEATURES


def test_work_budget_default_upgrade_preserves_custom_and_new_explicit_caps(tmp_path, monkeypatch):
    import json

    from src.agent_loop import AGENT_EFFORT_ROUNDS
    from src.agent_tools import MAX_AGENT_ROUNDS

    settings_file = tmp_path / "settings.json"
    monkeypatch.setattr(settings, "SETTINGS_FILE", str(settings_file))
    assert list(AGENT_EFFORT_ROUNDS.values()) == [20, 40, 80, 120, 200]
    assert MAX_AGENT_ROUNDS == 80
    for saved, expected in [({}, 80), ({"agent_max_rounds": 20}, 80),
                            ({"agent_max_rounds": 40}, 40),
                            ({"agent_max_rounds": 20, "agent_work_budget_version": 2}, 20)]:
        settings_file.write_text(json.dumps(saved))
        settings._invalidate_caches()
        assert settings.load_settings()["agent_max_rounds"] == expected
    settings.save_settings({"agent_max_rounds": 20})
    assert settings.load_settings()["agent_max_rounds"] == 20
    settings._invalidate_caches()
