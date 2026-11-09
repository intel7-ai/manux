"""Studio is the default screen; only a deliberate --ui choice keeps the standard one."""
from __future__ import annotations

import pytest

pytest.importorskip("PySide6")
from PySide6.QtCore import QSettings  # noqa: E402

from manux.ui.bridge import choose_layout  # noqa: E402


def prefs(tmp_path, text=None):
    path = tmp_path / "ui.ini"
    if text is not None:
        path.write_text(text, encoding="utf-8")
    return QSettings(str(path), QSettings.Format.IniFormat)


def test_new_install_opens_studio(tmp_path):
    assert choose_layout(prefs(tmp_path)) == "studio"


def test_standard_saved_by_older_versions_moves_to_studio(tmp_path):
    assert choose_layout(prefs(tmp_path, "[General]\nlayout=standard\ntutorialDone=true\n")) == "studio"


def test_explicit_choice_is_remembered(tmp_path):
    p = prefs(tmp_path)
    assert choose_layout(p, "standard") == "standard"
    p.setValue("layout", "standard")
    p.sync()
    assert choose_layout(prefs(tmp_path)) == "standard"
    assert choose_layout(prefs(tmp_path), "studio") == "studio"
