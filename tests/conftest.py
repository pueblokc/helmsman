"""Shared fixtures: isolated config dir, fake keyring, fake NVR, live local server."""
import http.client
import json
import sys
import threading
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import helmsman  # noqa: E402

CAM = "65f0c0ffee0123456789abcd"


@pytest.fixture(autouse=True)
def isolated_config(tmp_path, monkeypatch):
    cfg_dir = tmp_path / "cfg"
    monkeypatch.setattr(helmsman, "CONFIG_DIR", cfg_dir)
    monkeypatch.setattr(helmsman, "CONFIG_FILE", cfg_dir / "config.json")
    for var in ("DEFAULT_NVR_IP", "DEFAULT_USERNAME", "DEFAULT_PASSWORD"):
        monkeypatch.setattr(helmsman, var, "")
    monkeypatch.setattr(helmsman, "TELEGRAM_BOT", "")
    monkeypatch.setattr(helmsman, "ALLOWED_HOSTS", set())
    monkeypatch.setattr(helmsman, "nvr", helmsman.Nvr(""))
    return cfg_dir


class FakeKeyring:
    def __init__(self):
        self.store = {}

    def set_password(self, svc, user, pw):
        self.store[(svc, user)] = pw

    def get_password(self, svc, user):
        return self.store.get((svc, user))

    def delete_password(self, svc, user):
        self.store.pop((svc, user), None)


@pytest.fixture
def fake_keyring(monkeypatch):
    kr = FakeKeyring()
    monkeypatch.setattr(helmsman, "_keyring", kr)
    monkeypatch.setattr(helmsman, "HAVE_KEYRING", True)
    return kr


@pytest.fixture
def no_keyring(monkeypatch):
    monkeypatch.setattr(helmsman, "_keyring", None)
    monkeypatch.setattr(helmsman, "HAVE_KEYRING", False)


class FakeNvr:
    """Stands in for helmsman.Nvr; records calls, never touches the network."""

    instances = []

    def __init__(self, ip=""):
        self.ip = ip
        self.connected = False
        self.closed = False
        self.calls = []
        self.login_result = (True, "ok")
        self.bootstrap_result = (200, {"cameras": [
            {"id": CAM, "name": " PTZ ", "type": "UVC G5 PTZ", "marketName": "G5 PTZ",
             "state": "CONNECTED", "featureFlags": {"isPtz": True}},
            {"id": "aaaaaaaaaaaaaaaaaaaaaaaa", "name": "Bullet", "featureFlags": {}},
        ]}, "")
        self.raise_on = None
        FakeNvr.instances.append(self)

    def _rec(self, name, *args):
        self.calls.append((name, args))
        if self.raise_on == name:
            import requests
            raise requests.ConnectionError("boom")

    def close(self):
        self.closed = True
        self.connected = False

    def login(self, user, pw):
        self._rec("login", user, pw)
        ok, msg = self.login_result
        self.connected = ok
        return ok, msg

    def bootstrap(self):
        self._rec("bootstrap")
        return self.bootstrap_result

    def position(self, cam):
        self._rec("position", cam)
        return 200, {"degree": {"pan": 1, "tilt": 2, "zoom": 1.0}}, ""

    def list_presets(self, cam):
        self._rec("list_presets", cam)
        return 200, {"presets": [{"slot": 1, "name": "Gate"}, {"name": "noslot"}, "junk"]}, ""

    def move(self, cam, payload):
        self._rec("move", cam, payload)
        return 200, '{"success":true}'

    def goto_preset(self, cam, slot):
        self._rec("goto_preset", cam, slot)
        return 200, ""

    def save_preset(self, cam, name):
        self._rec("save_preset", cam, name)
        return 200, "{}"

    def rename_preset(self, cam, slot, name):
        self._rec("rename_preset", cam, slot, name)
        return 200, ""

    def delete_preset(self, cam, slot):
        self._rec("delete_preset", cam, slot)
        return 200, ""

    def snapshot(self, cam, hires=False):
        self._rec("snapshot", cam, hires)
        return 200, "text/html", b"\xff\xd8jpeg"

    def locate(self, cam):
        self._rec("locate", cam)
        return 200, ""

    def flashlight(self, cam, enable):
        self._rec("flashlight", cam, enable)
        return 200, ""

    def patch_camera(self, cam, patch):
        self._rec("patch_camera", cam, patch)
        return 200, "{}"

    def get_camera(self, cam):
        self._rec("get_camera", cam)
        return 200, {"id": cam, "name": "PTZ", "ispSettings": {"irLedMode": "auto"},
                     "featureFlags": {"hasSpotlight": True}}, ""


@pytest.fixture
def fake_nvr_cls(monkeypatch):
    FakeNvr.instances = []
    monkeypatch.setattr(helmsman, "Nvr", FakeNvr)
    monkeypatch.setattr(helmsman, "nvr", FakeNvr(""))
    return FakeNvr


@pytest.fixture
def connected(fake_nvr_cls, monkeypatch):
    n = FakeNvr("nvr.example.lan")
    n.connected = True
    monkeypatch.setattr(helmsman, "nvr", n)
    return n


class Client:
    def __init__(self, port):
        self.port = port

    def request(self, method, path, body=None, headers=None):
        h = {"Host": f"127.0.0.1:{self.port}"}
        data = None
        if body is not None:
            data = body if isinstance(body, (bytes, str)) else json.dumps(body)
            h["Content-Type"] = "application/json"
        h.update(headers or {})
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        try:
            conn.request(method, path, body=data, headers=h)
            r = conn.getresponse()
            raw = r.read()
            try:
                parsed = json.loads(raw)
            except ValueError:
                parsed = raw
            return r.status, parsed, dict(r.getheaders())
        finally:
            conn.close()

    def get(self, path, **kw):
        return self.request("GET", path, **kw)

    def post(self, path, body=None, **kw):
        return self.request("POST", path, body=body if body is not None else {}, **kw)


@pytest.fixture
def server():
    httpd = helmsman.ReuseTCPServer(("127.0.0.1", 0), helmsman.Handler)
    t = threading.Thread(target=httpd.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True)
    t.start()
    try:
        yield Client(httpd.server_address[1])
    finally:
        httpd.shutdown()
        httpd.server_close()
