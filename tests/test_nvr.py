"""Nvr client: URLs, headers and payloads sent to Protect (requests session mocked)."""
import json

import helmsman


class Resp:
    def __init__(self, status=200, body="{}", headers=None, content=b""):
        self.status_code = status
        self.text = body
        self.headers = headers or {}
        self.content = content

    def json(self):
        return json.loads(self.text)


class Sess:
    def __init__(self, responses=None):
        self.calls = []
        self.responses = responses or {}
        self.closed = False

    def _do(self, method, url, **kw):
        self.calls.append((method, url, kw))
        return self.responses.get((method, url.split("/", 3)[-1]), Resp())

    def get(self, url, **kw): return self._do("GET", url, **kw)
    def post(self, url, **kw): return self._do("POST", url, **kw)
    def patch(self, url, **kw): return self._do("PATCH", url, **kw)
    def delete(self, url, **kw): return self._do("DELETE", url, **kw)
    def close(self): self.closed = True


def make(responses=None):
    n = helmsman.Nvr("nvr.example.lan")
    n.s = Sess(responses)
    return n


def test_login_success_captures_csrf():
    n = make({("POST", "api/auth/login"): Resp(200, "{}", {"X-CSRF-Token": "tok"})})
    assert n.login("u", "p") == (True, "ok")
    assert n.connected and n.csrf == "tok"
    method, url, kw = n.s.calls[1]
    assert url == "https://nvr.example.lan/api/auth/login"
    assert kw["json"] == {"username": "u", "password": "p", "rememberMe": False}


def test_login_failure():
    n = make({("POST", "api/auth/login"): Resp(403, "AUTHENTICATION_FAILED")})
    ok, msg = n.login("u", "bad")
    assert not ok and "403" in msg and not n.connected


def test_login_exception_is_reported():
    n = make()
    def boom(*a, **k): raise OSError("unreachable")
    n.s.get = boom
    assert n.login("u", "p") == (False, "unreachable")


def test_move_sends_json_with_csrf():
    n = make()
    n.csrf = "tok"
    n.move("cam1", {"type": "continuous", "payload": {"x": 1}})
    method, url, kw = n.s.calls[0]
    assert url == "https://nvr.example.lan/proxy/protect/api/cameras/cam1/move"
    assert kw["headers"] == {"X-CSRF-Token": "tok", "Content-Type": "application/json"}
    assert json.loads(kw["data"]) == {"type": "continuous", "payload": {"x": 1}}


def test_bootstrap_non_json():
    n = make({("GET", "proxy/protect/api/bootstrap"): Resp(200, "<html>")})
    assert n.bootstrap() == (200, None, "<html>")


def test_preset_routes():
    n = make()
    n.goto_preset("c", -1)
    n.save_preset("c", "")
    n.rename_preset("c", 2, "x")
    n.delete_preset("c", 2)
    urls = [(m, u.split("/api/")[-1]) for m, u, _ in n.s.calls]
    assert urls == [("POST", "cameras/c/ptz/goto/-1"), ("POST", "cameras/c/ptz/preset"),
                    ("PATCH", "cameras/c/ptz/preset/2"), ("DELETE", "cameras/c/ptz/preset/2")]
    assert json.loads(n.s.calls[1][2]["data"]) == {}


def test_snapshot_hires_flag():
    n = make()
    n.snapshot("c", hires=True)
    assert n.s.calls[0][1].endswith("/cameras/c/snapshot?force=true")


def test_close():
    n = make()
    n.connected = True
    n.close()
    assert n.s.closed and not n.connected


def test_telegram_disabled_without_env(monkeypatch):
    called = []
    monkeypatch.setattr(helmsman.requests, "post", lambda *a, **k: called.append(1))
    helmsman.telegram_notify("hi")
    assert called == []


def test_telegram_throttled(monkeypatch):
    called = []
    monkeypatch.setattr(helmsman, "TELEGRAM_BOT", "dummy-bot")
    monkeypatch.setattr(helmsman, "TELEGRAM_CHAT", "dummy-chat")
    monkeypatch.setattr(helmsman, "_telegram_last", 0.0)
    monkeypatch.setattr(helmsman.requests, "post", lambda *a, **k: called.append(k["json"]))
    helmsman.telegram_notify("one")
    helmsman.telegram_notify("two")
    assert called == [{"chat_id": "dummy-chat", "text": "one"}]
