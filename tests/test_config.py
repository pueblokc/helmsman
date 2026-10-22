"""Config persistence: atomic/0600 writes, forget, keyring handling, cam settings."""
import json
import os
import stat
import sys
import threading

import pytest

import helmsman


def test_load_config_missing_returns_empty():
    assert helmsman.load_config() == {}


def test_load_config_corrupt_returns_empty(isolated_config):
    isolated_config.mkdir()
    helmsman.CONFIG_FILE.write_text("{not json", encoding="utf-8")
    assert helmsman.load_config() == {}


def test_save_config_roundtrip_and_no_tempfiles(isolated_config):
    helmsman.save_config({"ip": "nvr.example.lan", "user": "u"})
    assert helmsman.load_config() == {"ip": "nvr.example.lan", "user": "u"}
    assert [p.name for p in isolated_config.iterdir()] == ["config.json"]


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX permissions")
def test_save_config_is_private_from_creation():
    helmsman.save_config({"pass": "x"})
    mode = stat.S_IMODE(os.stat(helmsman.CONFIG_FILE).st_mode)
    assert mode == 0o600


def test_save_config_failure_keeps_previous_file(monkeypatch):
    helmsman.save_config({"ip": "keep"})

    def boom(*a, **k):
        raise OSError("disk full")
    monkeypatch.setattr(helmsman.os, "replace", boom)
    with pytest.raises(OSError):
        helmsman.save_config({"ip": "new"})
    assert helmsman.load_config() == {"ip": "keep"}
    assert len(list(helmsman.CONFIG_FILE.parent.iterdir())) == 1


def test_forget_config_deletes_keyring_password(fake_keyring):
    # Regression: forget used to delete the file first, then read it to find the user,
    # so the keyring entry was never removed.
    helmsman.save_config({"ip": "nvr", "user": "alice", "save_password": True})
    fake_keyring.set_password(helmsman.KEYRING_SERVICE, "alice", "pw")
    helmsman.forget_config()
    assert not helmsman.CONFIG_FILE.exists()
    assert fake_keyring.store == {}


def test_forget_config_without_file_is_noop(fake_keyring):
    helmsman.forget_config()
    assert fake_keyring.store == {}


def test_effective_defaults_prefers_keyring(fake_keyring):
    helmsman.save_config({"ip": "nvr", "user": "alice", "save_password": True, "pass": "old-file"})
    fake_keyring.set_password(helmsman.KEYRING_SERVICE, "alice", "from-keyring")
    d = helmsman.effective_defaults()
    assert d["pass"] == "from-keyring" and d["saved"] is True


def test_effective_defaults_file_fallback(no_keyring):
    helmsman.save_config({"ip": "nvr", "user": "alice", "save_password": True, "pass": "pw"})
    assert helmsman.effective_defaults()["pass"] == "pw"


def test_effective_defaults_ignores_password_when_not_saved(no_keyring):
    helmsman.save_config({"ip": "nvr", "user": "alice", "save_password": False, "pass": "stale"})
    assert helmsman.effective_defaults()["pass"] == ""


def test_effective_defaults_env_wins(monkeypatch, no_keyring):
    helmsman.save_config({"ip": "saved", "user": "saved"})
    monkeypatch.setattr(helmsman, "DEFAULT_NVR_IP", "env-nvr")
    d = helmsman.effective_defaults()
    assert d["ip"] == "env-nvr" and d["user"] == "saved"


def test_update_cam_settings_filters_and_coerces():
    cur = helmsman.update_cam_settings("cam1", {"invertX": 1, "evil": "x", "invertY": False})
    assert cur == {"invertX": True, "invertY": False}
    cur = helmsman.update_cam_settings("cam1", {"invertZ": True})
    assert cur == {"invertX": True, "invertY": False, "invertZ": True}
    assert json.loads(helmsman.CONFIG_FILE.read_text())["cam_settings"]["cam1"]["invertZ"] is True


def test_update_cam_settings_concurrent_writes_not_lost():
    def worker(i):
        helmsman.update_cam_settings(f"cam{i}", {"invertX": True})
    threads = [threading.Thread(target=worker, args=(i,)) for i in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(helmsman.load_config()["cam_settings"]) == 20
