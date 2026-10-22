"""End-to-end tests of the local HTTP API against a fake NVR (no network)."""
import helmsman
from conftest import CAM


# ---------- static page + guards ----------

def test_index_served_with_anti_framing_headers(server):
    code, body, hdrs = server.get("/")
    assert code == 200 and b"HELMSMAN" in body
    assert hdrs["X-Frame-Options"] == "DENY"
    assert "frame-ancestors 'none'" in hdrs["Content-Security-Policy"]


def test_dns_rebinding_host_rejected(server):
    code, body, _ = server.get("/api/config", headers={"Host": "rebind.attacker.example:8765"})
    assert code == 403


def test_cross_origin_post_rejected(server, connected):
    code, _, _ = server.post("/api/move", {"id": CAM, "payload": {"type": "continuous", "payload": {"x": 1}}},
                             headers={"Origin": "https://evil.example"})
    assert code == 403
    assert connected.calls == []


def test_cross_origin_forget_rejected(server, no_keyring):
    helmsman.save_config({"ip": "nvr", "user": "u"})
    code, _, _ = server.post("/api/forget", headers={"Origin": "https://evil.example"})
    assert code == 403
    assert helmsman.CONFIG_FILE.exists()


def test_same_origin_post_allowed(server, connected):
    code, body, _ = server.post("/api/move", {"id": CAM, "payload": {"type": "continuous", "payload": {"x": 0}}},
                                headers={"Origin": f"http://127.0.0.1:{server.port}"})
    assert code == 200 and body == {"success": True}


def test_unknown_routes_404(server):
    assert server.get("/nope")[0] == 404
    assert server.post("/api/nope")[0] == 401  # camera routes need a connection first


# ---------- connect ----------

def test_connect_requires_fields(server, fake_nvr_cls):
    assert server.post("/api/connect", {"ip": "nvr"})[0] == 400


def test_connect_rejects_url_injection(server, fake_nvr_cls):
    code, body, _ = server.post("/api/connect", {"ip": "evil.example/x?", "user": "u", "pass": "p"})
    assert code == 400
    assert len(fake_nvr_cls.instances) == 1  # only the fixture's idle instance


def test_connect_success_lists_cameras_and_saves_config(server, fake_nvr_cls, no_keyring):
    code, body, _ = server.post("/api/connect", {"ip": "nvr.example.lan", "user": "u", "pass": "p",
                                                 "remember": True, "remember_password": False})
    assert code == 200
    cams = body["cameras"]
    assert cams[0] == {"id": CAM, "name": "PTZ", "type": "UVC G5 PTZ", "market": "G5 PTZ",
                       "state": "CONNECTED", "ptz": True}
    assert cams[1]["ptz"] is False
    cfg = helmsman.load_config()
    assert cfg["ip"] == "nvr.example.lan" and "pass" not in cfg
    assert helmsman.nvr.ip == "nvr.example.lan"


def test_connect_file_fallback_stores_password(server, fake_nvr_cls, no_keyring):
    server.post("/api/connect", {"ip": "nvr", "user": "u", "pass": "p", "remember": True, "remember_password": True})
    assert helmsman.load_config()["pass"] == "p"


def test_connect_keyring_and_unsave_clears_it(server, fake_nvr_cls, fake_keyring):
    server.post("/api/connect", {"ip": "nvr", "user": "u", "pass": "p", "remember": True, "remember_password": True})
    assert "pass" not in helmsman.load_config()
    assert fake_keyring.store == {("Helmsman", "u"): "p"}
    server.post("/api/connect", {"ip": "nvr", "user": "u", "pass": "p", "remember": True, "remember_password": False})
    assert fake_keyring.store == {}


def test_connect_keeps_cam_settings(server, fake_nvr_cls, no_keyring):
    helmsman.update_cam_settings(CAM, {"invertX": True})
    server.post("/api/connect", {"ip": "nvr", "user": "u", "pass": "p", "remember": True})
    assert helmsman.load_config()["cam_settings"][CAM] == {"invertX": True}


def test_connect_login_failure_keeps_previous_session(server, fake_nvr_cls, monkeypatch):
    prev = helmsman.nvr

    class Failing(fake_nvr_cls):
        def __init__(self, ip=""):
            super().__init__(ip)
            self.login_result = (False, "HTTP 403")
    monkeypatch.setattr(helmsman, "Nvr", Failing)
    code, body, _ = server.post("/api/connect", {"ip": "nvr", "user": "u", "pass": "bad"})
    assert code == 401 and body["error"] == "HTTP 403"
    assert helmsman.nvr is prev


def test_connect_bootstrap_non_json_is_error_not_200(server, fake_nvr_cls, monkeypatch):
    # Regression: a 200 with an HTML body used to be returned as 200 {"error":...}; the UI
    # then treated it as success and crashed reading r.body.cameras.
    class HtmlBootstrap(fake_nvr_cls):
        def bootstrap(self):
            return 200, None, "<html>login</html>"
    monkeypatch.setattr(helmsman, "Nvr", HtmlBootstrap)
    code, _, _ = server.post("/api/connect", {"ip": "nvr", "user": "u", "pass": "p"})
    assert code == 502


def test_reconnect_closes_previous_session(server, fake_nvr_cls):
    server.post("/api/connect", {"ip": "nvr", "user": "u", "pass": "p"})
    first = helmsman.nvr
    server.post("/api/connect", {"ip": "nvr", "user": "u", "pass": "p"})
    assert first.closed and helmsman.nvr is not first


# ---------- camera routes ----------

def test_routes_require_connection(server, fake_nvr_cls):
    # Regression: /api/position and /api/presets hit https:/// and dropped the socket.
    assert server.get(f"/api/position?id={CAM}")[0] == 401
    code, body, _ = server.get(f"/api/presets?id={CAM}")
    assert code == 401 and body["presets"] == []


def test_bad_camera_id_rejected_before_nvr(server, connected):
    for path in ("/api/position?id=../users/x", "/api/camera?id=a%2Fb", "/api/snapshot?id="):
        assert server.get(path)[0] == 400
    assert server.post("/api/camera_patch", {"id": "../../users/self", "patch": {}})[0] == 400
    assert server.post("/api/preset?id=../x&slot=1")[0] == 400
    assert connected.calls == []


def test_position(server, connected):
    code, body, _ = server.get(f"/api/position?id={CAM}")
    assert code == 200 and body["degree"]["pan"] == 1


def test_presets_tolerates_junk(server, connected):
    code, body, _ = server.get(f"/api/presets?id={CAM}")
    assert body["presets"] == [{"slot": 1, "name": "Gate"}, {"slot": 2, "name": "noslot"}]


def test_snapshot_forces_image_content_type(server, connected):
    code, body, hdrs = server.get(f"/api/snapshot?id={CAM}&hi=1")
    assert code == 200 and body == b"\xff\xd8jpeg"
    assert hdrs["Content-Type"] == "image/jpeg"
    assert connected.calls == [("snapshot", (CAM, True))]


def test_camera_info(server, connected):
    code, body, _ = server.get(f"/api/camera?id={CAM}")
    assert body["irLedMode"] == "auto" and body["hasFlashlight"] is True


def test_move_forwards_payload(server, connected):
    p = {"type": "continuous", "payload": {"x": 500, "y": 0, "z": 0}}
    server.post("/api/move", {"id": CAM, "payload": p})
    assert connected.calls == [("move", (CAM, p))]


def test_move_rejects_non_object_payload(server, connected):
    assert server.post("/api/move", {"id": CAM, "payload": "x"})[0] == 400


def test_preset_goto_home_slot(server, connected):
    assert server.post(f"/api/preset?id={CAM}&slot=-1")[0] == 200
    assert connected.calls == [("goto_preset", (CAM, -1))]


def test_rename_preset_bad_slot_is_400_not_crash(server, connected):
    # Regression: int("abc") raised inside the handler and the socket was dropped.
    assert server.post("/api/rename_preset", {"id": CAM, "slot": "abc", "name": "x"})[0] == 400
    assert server.post("/api/delete_preset", {"id": CAM, "slot": None})[0] == 400


def test_preset_crud(server, connected):
    server.post("/api/save_preset", {"id": CAM, "name": " Gate "})
    server.post("/api/rename_preset", {"id": CAM, "slot": 2, "name": "Door"})
    server.post("/api/delete_preset", {"id": CAM, "slot": "3"})
    assert connected.calls == [("save_preset", (CAM, "Gate")), ("rename_preset", (CAM, 2, "Door")),
                               ("delete_preset", (CAM, 3))]


def test_locate_and_flashlight(server, connected):
    server.post("/api/locate", {"id": CAM})
    server.post("/api/flashlight", {"id": CAM, "enable": True})
    assert connected.calls == [("locate", (CAM,)), ("flashlight", (CAM, True))]


def test_camera_patch_only_ir_mode(server, connected):
    assert server.post("/api/camera_patch", {"id": CAM, "patch": {"ispSettings": {"irLedMode": "on"}}})[0] == 200
    for bad in ({"recordingSettings": {"mode": "never"}},
                {"ispSettings": {"irLedMode": "on", "brightness": 0}},
                {"ispSettings": {"irLedMode": "laser"}},
                "x"):
        assert server.post("/api/camera_patch", {"id": CAM, "patch": bad})[0] == 400
    assert connected.calls == [("patch_camera", (CAM, {"ispSettings": {"irLedMode": "on"}}))]


def test_cam_settings_endpoint(server):
    code, body, _ = server.post("/api/cam_settings", {"id": CAM, "patch": {"invertY": True}})
    assert code == 200 and body["settings"] == {"invertY": True}
    assert server.post("/api/cam_settings", {"id": "../x", "patch": {}})[0] == 400


def test_nvr_network_error_returns_502(server, connected):
    connected.raise_on = "move"
    code, body, _ = server.post("/api/move", {"id": CAM, "payload": {"type": "continuous", "payload": {}}})
    assert code == 502 and "ConnectionError" in body["error"]


def test_malformed_bodies_do_not_crash(server, connected):
    assert server.request("POST", "/api/move", body=b"[1,2]")[0] == 400
    assert server.request("POST", "/api/move", body=b"{bad")[0] == 400
    assert server.request("POST", "/api/move", body=b"{}", headers={"Content-Length": "abc"})[0] in (400, 401)


def test_config_endpoint_and_forget(server, no_keyring):
    helmsman.save_config({"ip": "nvr", "user": "u", "save_password": True, "pass": "p"})
    code, body, _ = server.get("/api/config")
    assert body["nvr"] == "nvr" and body["pass"] == "p" and body["saved"] is True
    assert server.post("/api/forget")[0] == 200
    assert server.get("/api/config")[1]["saved"] is False
