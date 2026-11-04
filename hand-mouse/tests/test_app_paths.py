from src import app_paths


def _installed(monkeypatch, tmp_path, bundled_text='{"a": 1}'):
    bundled = tmp_path / "app" / "gestures.json"
    bundled.parent.mkdir()
    bundled.write_text(bundled_text, encoding="utf-8")
    monkeypatch.setattr(app_paths, "FROZEN", True)
    monkeypatch.setattr(app_paths, "BUNDLED_CONFIG", bundled)
    monkeypatch.setattr(app_paths, "CONFIG_DIR", tmp_path / "data" / "config")
    return bundled


def test_from_source_uses_the_repo_config():
    assert app_paths.FROZEN is False
    assert app_paths.user_config_path() == app_paths.BUNDLED_CONFIG
    assert app_paths.LOG_DIR == app_paths.APP_DIR / "logs"


def test_installed_copies_the_bundled_config_on_first_run(monkeypatch, tmp_path):
    _installed(monkeypatch, tmp_path)
    path = app_paths.user_config_path()
    assert path == tmp_path / "data" / "config" / "gestures.json"
    assert path.read_text(encoding="utf-8") == '{"a": 1}'


def test_update_replaces_an_untouched_config(monkeypatch, tmp_path):
    bundled = _installed(monkeypatch, tmp_path)
    path = app_paths.user_config_path()
    bundled.write_text('{"a": 2}', encoding="utf-8")          # a new release ships new defaults
    assert app_paths.user_config_path().read_text(encoding="utf-8") == '{"a": 2}'
    assert path.exists()


def test_update_keeps_a_config_the_user_changed(monkeypatch, tmp_path):
    bundled = _installed(monkeypatch, tmp_path)
    path = app_paths.user_config_path()
    path.write_text('{"a": 9}', encoding="utf-8")             # saved from the settings panel
    bundled.write_text('{"a": 2}', encoding="utf-8")
    assert app_paths.user_config_path().read_text(encoding="utf-8") == '{"a": 9}'
