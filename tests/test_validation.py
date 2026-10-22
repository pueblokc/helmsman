"""Pure helpers: input validation, Host/Origin guards, browser URL."""
import pytest

import helmsman


@pytest.mark.parametrize("cam", ["65f0c0ffee0123456789abcd", "abc_DEF-1"])
def test_valid_cam_id(cam):
    assert helmsman.valid_cam_id(cam)


@pytest.mark.parametrize("cam", ["", None, 5, "../users", "a/b", "a?b", "a#b", "a%2fb", "x" * 65, "a b"])
def test_invalid_cam_id(cam):
    assert not helmsman.valid_cam_id(cam)


@pytest.mark.parametrize("ip", ["192.0.2.10", "nvr.example.lan", "nvr", "nvr.example.lan:7443", "[2001:db8::1]", "[2001:db8::1]:443"])
def test_valid_nvr_host(ip):
    assert helmsman.valid_nvr_host(ip)


@pytest.mark.parametrize("ip", ["", "https://nvr", "nvr/path", "user@nvr", "nvr?x", "nvr#x", "nvr:abc", "a b", None])
def test_invalid_nvr_host(ip):
    assert not helmsman.valid_nvr_host(ip)


@pytest.mark.parametrize("host", ["127.0.0.1:8765", "127.0.0.1", "localhost:8765", "LOCALHOST", "[::1]:8765", "192.0.2.5:8765"])
def test_host_allowed(host):
    assert helmsman.host_allowed(host)


@pytest.mark.parametrize("host", ["", "attacker.example:8765", "rebind.attacker.example", "127.0.0.1.nip.io:8765"])
def test_host_rejected(host):
    assert not helmsman.host_allowed(host)


def test_host_allowed_via_env_list(monkeypatch):
    monkeypatch.setattr(helmsman, "ALLOWED_HOSTS", {"cams.example.lan"})
    assert helmsman.host_allowed("cams.example.lan:443")


def test_origin_allowed():
    assert helmsman.origin_allowed("", "127.0.0.1:8765")                      # curl / scripts
    assert helmsman.origin_allowed("http://127.0.0.1:8765", "127.0.0.1:8765")
    assert not helmsman.origin_allowed("https://evil.example", "127.0.0.1:8765")
    assert not helmsman.origin_allowed("http://127.0.0.1:9999", "127.0.0.1:8765")
    assert not helmsman.origin_allowed("null", "127.0.0.1:8765")


def test_origin_allowed_proxy_name(monkeypatch):
    monkeypatch.setattr(helmsman, "ALLOWED_HOSTS", {"cams.example.lan"})
    assert helmsman.origin_allowed("https://cams.example.lan", "127.0.0.1:8765")


@pytest.mark.parametrize("bind,url", [
    ("127.0.0.1", "http://127.0.0.1:8765/"),
    ("0.0.0.0", "http://127.0.0.1:8765/"),
    ("::", "http://127.0.0.1:8765/"),
    # Regression: a specific LAN bind used to open 127.0.0.1, where nothing listens.
    ("192.0.2.7", "http://192.0.2.7:8765/"),
    ("::1", "http://[::1]:8765/"),
])
def test_browser_url(bind, url):
    assert helmsman.browser_url(bind, 8765) == url


@pytest.mark.parametrize("v,ok", [(1, 1), ("3", 3), (-1, -1), ("x", None), (-2, None), (True, None), (None, None)])
def test_parse_slot(v, ok):
    if ok is None:
        with pytest.raises((ValueError, TypeError)):
            helmsman._parse_slot(v)
    else:
        assert helmsman._parse_slot(v) == ok
