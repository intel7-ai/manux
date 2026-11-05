"""Updater and build info without Velopack or a network (a fake velopack module stands in)."""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from manux.desktop import default_hand_model, self_check
from manux.platform.updater import BuildInfo, Updater, read_build_info
from manux.settings.paths import resolve

APP_DIR = Path(__file__).resolve().parents[3]


class FakeManager:
    def __init__(self, newer):
        self.newer = newer
        self.calls = []

    def check_for_updates(self):
        self.calls.append("check")
        return None if self.newer is None else SimpleNamespace(TargetFullRelease=SimpleNamespace(Version=self.newer))

    def download_updates(self, info):
        self.calls.append("download")

    def wait_exit_then_apply_updates(self, info, silent, restart):
        self.calls.append(("apply", silent, restart))


def fake_velopack(manager):
    return SimpleNamespace(GithubSource=lambda url: url, UpdateManager=lambda source: manager)


def release(version="0.4.3"):
    return BuildInfo(version=version, pack_id="MANUX", channel="win", updates=True)


def test_build_info_defaults_when_running_from_source(tmp_path):
    info = read_build_info(tmp_path)
    assert info.updates is False and info.pack_id == "" and info.version == "0.4.0.dev0"


def test_build_info_reads_what_build_ps1_writes(tmp_path):
    (tmp_path / "build_info.json").write_text(json.dumps(
        {"version": "0.4.12-pr70", "pack_id": "MANUXPreview", "channel": "preview", "updates": False}), encoding="utf-8")
    info = read_build_info(tmp_path)
    assert (info.version, info.pack_id, info.updates) == ("0.4.12-pr70", "MANUXPreview", False)
    assert info.label == "0.4.12-pr70 (MANUXPreview)"
    assert release().label == "0.4.3"


def test_source_runs_and_preview_builds_never_check():
    manager = FakeManager("9.9.9")
    for updater in (Updater(release(), frozen=False, velopack=fake_velopack(manager), log=lambda _m: None),
                    Updater(BuildInfo(pack_id="MANUXPreview", updates=False), frozen=True,
                            velopack=fake_velopack(manager), log=lambda _m: None)):
        assert updater.start() is None
        updater.check()
        assert updater.status == ""
    assert manager.calls == []


def test_newer_release_downloads_and_applies_after_exit_without_restart():
    manager = FakeManager("0.4.9")
    updater = Updater(release(), frozen=True, velopack=fake_velopack(manager), log=lambda _m: None)
    updater.check()
    assert updater.status == "ready" and updater.latest_version == "0.4.9"
    assert manager.calls == ["check", "download", ("apply", True, False)]


def test_up_to_date_and_failures_are_reported_not_raised():
    current = Updater(release(), frozen=True, velopack=fake_velopack(FakeManager(None)), log=lambda _m: None)
    current.check()
    assert current.status == "current"

    class Offline(FakeManager):
        def check_for_updates(self):
            raise OSError("offline")

    offline = Updater(release(), frozen=True, velopack=fake_velopack(Offline(None)), log=lambda _m: None)
    offline.check()
    assert offline.status == "error: offline"


def test_the_hand_model_is_bundled_with_apps_manux():
    model = default_hand_model(resolve(env={}, frozen=False, app_dir=APP_DIR))
    assert model == APP_DIR / "models" / "hand_landmarker.task"
    assert model.stat().st_size > 1_000_000


def test_self_check_reports_each_failure(tmp_path):
    paths = resolve(env={"MANUX_DATA_DIR": str(tmp_path)}, frozen=False, app_dir=APP_DIR)
    out = tmp_path / "check.json"
    code = self_check(paths, tmp_path / "missing.task", BuildInfo(), out)
    result = json.loads(out.read_text(encoding="utf-8"))
    assert code == 1 and result["ok"] is False
    assert result["checks"]["hand_model"].startswith("FAIL")
    assert set(result["checks"]) == {"hand_model", "opencv", "mediapipe", "qt"}
