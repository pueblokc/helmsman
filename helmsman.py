"""
Helmsman — Virtual Joystick PTZ Controller for UniFi Protect
=============================================================
Drag the on-screen joystick → your PTZ camera moves. Same browser tab, no apps to install.

Run:
    python helmsman.py

then open the URL it prints (default: http://127.0.0.1:8765) and click Connect.

How:
- Spins up a tiny local HTTP server with the joystick UI inline.
- Logs into your UniFi Protect NVR (HTTPS, session cookie + CSRF).
- Forwards joystick deflection to the NVR's continuous-move endpoint at ~10 Hz.
- Server-side dead-man timer (~20s in Protect 7.1) auto-stops the camera if the bridge dies.

Verified Protect routes (Protect 7.1.46):
    POST /api/auth/login
    GET  /proxy/protect/api/bootstrap
    GET  /proxy/protect/api/cameras/{id}/ptz/position
    POST /proxy/protect/api/cameras/{id}/move
    POST /proxy/protect/api/cameras/{id}/ptz/goto/{slot}

Move payload (continuous mode):
    {"type":"continuous","payload":{"x":-1000..1000,"y":-1000..1000,"z":-1000..1000}}
    x = pan velocity  (server +x = pan right)
    y = tilt velocity (server +y = tilt down)
    z = zoom velocity (server +z = zoom in)
    {x:0,y:0,z:0} = stop

Other supported types (not used by this UI but the discriminator accepts them):
    relative  — payload {panPos:-4095..4095, tiltPos:..., panSpeed:0..1000, tiltSpeed:..., scale}
    absolute  — same fields, absolute targets
    zoom      — payload {zoomPos:0..730 (G5 PTZ), zoomSpeed:0..1000, scale}
    center    — payload {x:0..1000, y:0..1000, z:0..1000} (viewport-relative click target)
    preset    — payload {slot:int}

© 2026 KCCS — kccsonline.com
"""
from __future__ import annotations
import http.server, json, os, socketserver, stat, sys, threading, webbrowser
import urllib.parse
from pathlib import Path

try:
    import requests
    import urllib3
    urllib3.disable_warnings()
except ImportError:
    sys.exit("Install requests:  pip install requests")

# Optional: OS keyring for secure password storage. Falls back to plaintext file if unavailable.
try:
    import keyring as _keyring
    HAVE_KEYRING = True
except ImportError:
    _keyring = None
    HAVE_KEYRING = False

KEYRING_SERVICE = "Helmsman"


# Optional environment overrides — purely conveniences, all overridable in the web UI.
DEFAULT_NVR_IP   = os.environ.get("HELMSMAN_NVR_IP", "")
DEFAULT_USERNAME = os.environ.get("HELMSMAN_USER",   "")
DEFAULT_PASSWORD = os.environ.get("HELMSMAN_PASS",   "")
PORT             = int(os.environ.get("HELMSMAN_PORT", "8765"))
BIND             = os.environ.get("HELMSMAN_BIND", "127.0.0.1")

CONFIG_DIR  = Path(os.environ.get("HELMSMAN_CONFIG_DIR", str(Path.home() / ".helmsman")))
CONFIG_FILE = CONFIG_DIR / "config.json"


def load_config() -> dict:
    """Read saved config from disk. Returns {} on miss."""
    try:
        return json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_config(cfg: dict) -> None:
    """Write config to disk with restrictive permissions."""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_FILE.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
    try:
        os.chmod(CONFIG_FILE, stat.S_IRUSR | stat.S_IWUSR)
    except Exception:
        pass


def forget_config() -> None:
    try:
        CONFIG_FILE.unlink()
    except FileNotFoundError:
        pass
    if HAVE_KEYRING:
        try:
            cfg = load_config()
            user = cfg.get("user")
            if user:
                _keyring.delete_password(KEYRING_SERVICE, user)
        except Exception:
            pass


def store_password(user: str, pw: str) -> str:
    """Persist password. Returns 'keyring' or 'file' depending on backend used."""
    if HAVE_KEYRING and user:
        try:
            _keyring.set_password(KEYRING_SERVICE, user, pw)
            return "keyring"
        except Exception:
            pass
    return "file"


def fetch_password(user: str) -> str:
    if HAVE_KEYRING and user:
        try:
            v = _keyring.get_password(KEYRING_SERVICE, user)
            if v:
                return v
        except Exception:
            pass
    return ""


def effective_defaults() -> dict:
    """Env vars win over saved config (for explicit overrides at launch)."""
    saved = load_config()
    user = saved.get("user", "")
    pw = ""
    if saved.get("save_password"):
        # Try keyring first, fall back to plaintext file
        pw = fetch_password(user) or saved.get("pass", "")
    return {
        "ip":   DEFAULT_NVR_IP   or saved.get("ip", ""),
        "user": DEFAULT_USERNAME or user,
        "pass": DEFAULT_PASSWORD or pw,
        "saved": bool(saved.get("ip") or saved.get("user") or saved.get("pass") or pw),
        "cam_settings": saved.get("cam_settings", {}),  # per-camera axis flips etc.
    }


def update_cam_settings(cam_id: str, patch: dict) -> dict:
    cfg = load_config()
    cs = cfg.setdefault("cam_settings", {})
    cur = cs.get(cam_id, {})
    cur.update(patch)
    cs[cam_id] = cur
    save_config(cfg)
    return cur


class Nvr:
    def __init__(self, ip: str):
        self.ip = ip
        self.s = requests.Session()
        self.s.verify = False
        self.csrf = ""
        self.connected = False

    def login(self, user: str, pw: str) -> tuple[bool, str]:
        try:
            self.s.get(f"https://{self.ip}/", timeout=10)
            r = self.s.post(
                f"https://{self.ip}/api/auth/login",
                json={"username": user, "password": pw, "rememberMe": False},
                timeout=10,
            )
            if r.status_code != 200:
                return False, f"HTTP {r.status_code}: {r.text[:200]}"
            self.csrf = r.headers.get("X-CSRF-Token", "")
            self.connected = True
            return True, "ok"
        except Exception as e:
            return False, str(e)

    def _hdrs(self, content_type: str = ""):
        h = {"X-CSRF-Token": self.csrf} if self.csrf else {}
        if content_type:
            h["Content-Type"] = content_type
        return h

    def bootstrap(self):
        r = self.s.get(f"https://{self.ip}/proxy/protect/api/bootstrap",
                       headers=self._hdrs(), timeout=15)
        try:
            return r.status_code, r.json(), r.text
        except Exception:
            return r.status_code, None, r.text

    def position(self, cam_id: str):
        r = self.s.get(f"https://{self.ip}/proxy/protect/api/cameras/{cam_id}/ptz/position",
                       headers=self._hdrs(), timeout=10)
        try:
            return r.status_code, r.json(), r.text
        except Exception:
            return r.status_code, None, r.text

    def move(self, cam_id: str, payload: dict):
        r = self.s.post(f"https://{self.ip}/proxy/protect/api/cameras/{cam_id}/move",
                        headers=self._hdrs("application/json"),
                        data=json.dumps(payload), timeout=10)
        return r.status_code, r.text

    def goto_preset(self, cam_id: str, slot: int):
        r = self.s.post(f"https://{self.ip}/proxy/protect/api/cameras/{cam_id}/ptz/goto/{slot}",
                        headers=self._hdrs(), timeout=10)
        return r.status_code, r.text

    def list_presets(self, cam_id: str):
        r = self.s.get(f"https://{self.ip}/proxy/protect/api/cameras/{cam_id}/ptz/preset",
                       headers=self._hdrs(), timeout=10)
        try:
            return r.status_code, r.json(), r.text
        except Exception:
            return r.status_code, None, r.text

    def save_preset(self, cam_id: str, name: str = ""):
        body = {"name": name} if name else {}
        r = self.s.post(f"https://{self.ip}/proxy/protect/api/cameras/{cam_id}/ptz/preset",
                        headers=self._hdrs("application/json"),
                        data=json.dumps(body), timeout=10)
        return r.status_code, r.text

    def rename_preset(self, cam_id: str, slot: int, name: str):
        r = self.s.patch(f"https://{self.ip}/proxy/protect/api/cameras/{cam_id}/ptz/preset/{slot}",
                         headers=self._hdrs("application/json"),
                         data=json.dumps({"name": name}), timeout=10)
        return r.status_code, r.text

    def delete_preset(self, cam_id: str, slot: int):
        r = self.s.delete(f"https://{self.ip}/proxy/protect/api/cameras/{cam_id}/ptz/preset/{slot}",
                          headers=self._hdrs(), timeout=10)
        return r.status_code, r.text

    def snapshot(self, cam_id: str, hires: bool = False):
        url = f"https://{self.ip}/proxy/protect/api/cameras/{cam_id}/snapshot"
        if hires:
            url += "?force=true"
        r = self.s.get(url, headers=self._hdrs(), timeout=15)
        return r.status_code, r.headers.get("Content-Type", "image/jpeg"), r.content

    def locate(self, cam_id: str):
        r = self.s.post(f"https://{self.ip}/proxy/protect/api/cameras/{cam_id}/locate",
                        headers=self._hdrs(), timeout=10)
        return r.status_code, r.text

    def flashlight(self, cam_id: str, enable: bool):
        r = self.s.post(f"https://{self.ip}/proxy/protect/api/cameras/{cam_id}/turnon-flashlight",
                        headers=self._hdrs("application/json"),
                        data=json.dumps({"enable": enable}), timeout=10)
        return r.status_code, r.text

    def patch_camera(self, cam_id: str, patch: dict):
        r = self.s.patch(f"https://{self.ip}/proxy/protect/api/cameras/{cam_id}",
                         headers=self._hdrs("application/json"),
                         data=json.dumps(patch), timeout=10)
        return r.status_code, r.text

    def get_camera(self, cam_id: str):
        r = self.s.get(f"https://{self.ip}/proxy/protect/api/cameras/{cam_id}",
                       headers=self._hdrs(), timeout=10)
        try:
            return r.status_code, r.json(), r.text
        except Exception:
            return r.status_code, None, r.text


nvr = Nvr("")

# Optional: Telegram notify hook. Set both env vars to enable.
TELEGRAM_BOT   = os.environ.get("HELMSMAN_TELEGRAM_BOT", "")
TELEGRAM_CHAT  = os.environ.get("HELMSMAN_TELEGRAM_CHAT", "")
_telegram_last = 0.0
_telegram_lock = threading.Lock()

def telegram_notify(msg: str, throttle_sec: float = 60.0) -> None:
    if not (TELEGRAM_BOT and TELEGRAM_CHAT):
        return
    global _telegram_last
    import time as _t
    with _telegram_lock:
        now = _t.time()
        if now - _telegram_last < throttle_sec:
            return
        _telegram_last = now
    try:
        requests.post(f"https://api.telegram.org/bot{TELEGRAM_BOT}/sendMessage",
                      json={"chat_id": TELEGRAM_CHAT, "text": msg}, timeout=5)
    except Exception:
        pass


HTML = r"""<!doctype html>
<html lang="en"><head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>Helmsman — UniFi Protect PTZ</title>
<style>
  :root {
    --bg: #1e1e2e; --surface: #313244; --surface2: #45475a;
    --text: #cdd6f4; --subtext: #a6adc8; --accent: #89b4fa; --green: #a6e3a1;
    --red: #f38ba8; --yellow: #f9e2af; --pink: #f5c2e7;
    --border: #585b70;
  }
  * { box-sizing: border-box; }
  html, body { margin: 0; padding: 0; background: var(--bg); color: var(--text);
    font-family: ui-monospace, "Cascadia Code", Consolas, monospace; height: 100%; }
  .layout { display: grid; grid-template-columns: 360px 1fr 360px; gap: 12px;
    padding: 12px; height: 100vh; }
  .panel { background: var(--surface); border: 1px solid var(--border); border-radius: 10px;
    padding: 14px; overflow: auto; }
  .center { display: flex; flex-direction: column; align-items: center; justify-content: center; }
  h1 { font-size: 14px; margin: 0 0 10px; color: var(--accent); letter-spacing: 0.05em; }
  h2 { font-size: 12px; margin: 14px 0 6px; color: var(--subtext); text-transform: uppercase; letter-spacing: 0.1em; }
  label { display: block; font-size: 11px; color: var(--subtext); margin: 8px 0 3px; }
  input, select, button { background: var(--surface2); color: var(--text); border: 1px solid var(--border);
    border-radius: 6px; padding: 8px 10px; font-family: inherit; font-size: 12px; width: 100%; }
  button { cursor: pointer; transition: 0.1s; }
  button:hover { background: var(--accent); color: #11111b; border-color: var(--accent); }
  button.danger { background: var(--red); color: #11111b; border-color: var(--red); font-weight: bold; }
  button.danger:hover { background: #ff6680; }
  button.preset { background: var(--surface2); }
  button.preset:hover { background: var(--pink); color: #11111b; border-color: var(--pink); }
  button:disabled { opacity: 0.4; cursor: not-allowed; }
  .row { display: flex; gap: 6px; }
  .grid3 { display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 6px; margin-top: 6px; }
  .badge { display: inline-block; padding: 2px 8px; border-radius: 4px; font-size: 10px;
    background: var(--surface2); border: 1px solid var(--border); }
  .badge.ok { background: var(--green); color: #11111b; border-color: var(--green); }
  .badge.bad { background: var(--red); color: #11111b; border-color: var(--red); }
  #stick { background: #11111b; border-radius: 50%; touch-action: none; cursor: grab; user-select: none;
    position: relative; z-index: 2; }
  #stick:active { cursor: grabbing; }
  .helm-wrap { position: relative; }
  #snap { position: absolute; left: 50%; top: 50%; transform: translate(-50%, -50%);
    width: 320px; height: 320px; border-radius: 50%; object-fit: cover; opacity: 0.5;
    z-index: 1; pointer-events: none; transition: opacity 0.3s; }
  #snap.live { opacity: 0.85; }
  #zoom { background: #11111b; border-radius: 8px; touch-action: none; cursor: ns-resize;
    user-select: none; width: 50px; }
  .axes { display: grid; grid-template-columns: auto 1fr auto; gap: 6px 10px;
    font-size: 11px; color: var(--subtext); margin-top: 12px; align-items: center;
    width: 380px; }
  .axis-bar { height: 8px; background: var(--surface2); border-radius: 4px; position: relative; overflow: hidden; }
  .axis-bar::before { content: ""; position: absolute; left: 50%; top: 0; bottom: 0;
    width: 1px; background: var(--border); }
  .axis-fill { position: absolute; top: 0; bottom: 0; background: var(--accent); }
  #log { font-size: 10px; background: #11111b; color: var(--subtext); padding: 8px;
    border-radius: 6px; height: 220px; overflow-y: scroll; white-space: pre-wrap;
    font-family: ui-monospace, monospace; line-height: 1.4; }
  .log-err { color: var(--red); }
  .log-ok { color: var(--green); }
  .log-info { color: var(--accent); }
  .stick-wrap { position: relative; display: flex; gap: 14px; align-items: center; }
  .footer { position: fixed; bottom: 0; left: 0; right: 0; padding: 4px 12px;
    font-size: 10px; color: var(--subtext); text-align: center;
    background: rgba(17,17,27,0.7); }
  .footer a { color: var(--accent); text-decoration: none; }
  .position { font-size: 11px; color: var(--subtext); margin-top: 8px; line-height: 1.6; }
  .key { display: inline-block; padding: 1px 6px; border: 1px solid var(--border);
    border-radius: 3px; font-size: 10px; background: var(--surface2); }
  pre { margin: 0; white-space: pre-wrap; word-break: break-all; }
  .brand { display: flex; align-items: center; gap: 10px; margin-bottom: 6px; }
  .brand svg { flex: none; }
</style>
</head><body>

<div class="layout">

  <div class="panel">
    <div class="brand">
      <svg viewBox="0 0 240 240" width="32" height="32" aria-hidden="true">
        <defs>
          <linearGradient id="brimg" x1="0%" y1="0%" x2="100%" y2="100%">
            <stop offset="0%" stop-color="#89b4fa"/><stop offset="100%" stop-color="#cba6f7"/>
          </linearGradient>
          <radialGradient id="bhubg" cx="50%" cy="50%" r="50%">
            <stop offset="0%" stop-color="#cba6f7"/><stop offset="60%" stop-color="#89b4fa"/><stop offset="100%" stop-color="#1e1e2e"/>
          </radialGradient>
        </defs>
        <g stroke="url(#brimg)" stroke-width="14" stroke-linecap="round" fill="none">
          <line x1="120" y1="22" x2="120" y2="58"/><line x1="120" y1="218" x2="120" y2="182"/>
          <line x1="22" y1="120" x2="58" y2="120"/><line x1="218" y1="120" x2="182" y2="120"/>
          <line x1="50" y1="50" x2="76" y2="76"/><line x1="190" y1="190" x2="164" y2="164"/>
          <line x1="190" y1="50" x2="164" y2="76"/><line x1="50" y1="190" x2="76" y2="164"/>
        </g>
        <circle cx="120" cy="120" r="78" fill="none" stroke="url(#brimg)" stroke-width="16"/>
        <g stroke="#cba6f7" stroke-width="6" stroke-linecap="round">
          <line x1="120" y1="58" x2="120" y2="92"/><line x1="120" y1="148" x2="120" y2="182"/>
          <line x1="58" y1="120" x2="92" y2="120"/><line x1="148" y1="120" x2="182" y2="120"/>
        </g>
        <circle cx="120" cy="120" r="36" fill="url(#bhubg)"/>
        <circle cx="120" cy="120" r="9" fill="#11111b"/>
        <circle cx="120" cy="120" r="9" fill="none" stroke="#89b4fa" stroke-width="2"/>
      </svg>
      <h1 style="margin:0">HELMSMAN</h1>
    </div>
    <div style="margin-top:6px">NVR <span class="badge" id="connBadge">disconnected</span>
      <span class="badge" id="padBadge" style="display:none">🎮 gamepad</span>
    </div>
    <label>NVR IP / hostname</label>
    <input id="nvrIp" placeholder="192.168.x.x"/>
    <label>Username</label>
    <input id="nvrUser" placeholder="local admin user"/>
    <label>Password</label>
    <input id="nvrPass" type="password" placeholder="••••••"/>
    <div class="row" style="margin-top:10px;gap:14px;align-items:center;font-size:11px;color:var(--subtext)">
      <label style="margin:0;display:flex;align-items:center;gap:6px;cursor:pointer">
        <input type="checkbox" id="remember" style="width:auto" checked/> remember
      </label>
      <label style="margin:0;display:flex;align-items:center;gap:6px;cursor:pointer">
        <input type="checkbox" id="rememberPass" style="width:auto"/> save password
      </label>
      <a href="#" id="forgetLink" style="display:none;color:var(--red);text-decoration:none;margin-left:auto">forget</a>
    </div>
    <div style="margin-top:10px"></div>
    <button id="btnConnect">Connect &amp; Bootstrap</button>

    <h2>Camera</h2>
    <select id="camSelect"><option>(connect first)</option></select>
    <div class="position" id="posDisplay">pan: — / tilt: — / zoom: —</div>

    <h2>Safety</h2>
    <label>Speed limit: <span id="speedLbl">100%</span></label>
    <input id="speed" type="range" min="0" max="100" value="100"/>
    <label>Deadzone: <span id="deadLbl">8%</span></label>
    <input id="dead" type="range" min="0" max="40" value="8"/>
    <label>Send rate: <span id="rateLbl">10 Hz</span></label>
    <input id="rate" type="range" min="2" max="20" value="10"/>

    <h2>Camera</h2>
    <div class="row" style="gap:6px;flex-wrap:wrap">
      <button id="btnLocate"   style="flex:1 1 30%">Locate (LED)</button>
      <button id="btnFlash"    style="flex:1 1 30%">Flashlight</button>
      <button id="btnSnap"     style="flex:1 1 30%">Live preview</button>
    </div>
    <label style="margin-top:10px">IR night mode</label>
    <select id="irMode">
      <option value="">—</option>
      <option value="auto">auto</option>
      <option value="on">on</option>
      <option value="off">off</option>
      <option value="autoFilterOnly">auto (filter only)</option>
    </select>
    <div class="row" style="gap:14px;margin-top:8px;font-size:11px;color:var(--subtext)">
      <label style="margin:0;display:flex;align-items:center;gap:6px;cursor:pointer">
        <input type="checkbox" id="invertX" style="width:auto"/> invert X
      </label>
      <label style="margin:0;display:flex;align-items:center;gap:6px;cursor:pointer">
        <input type="checkbox" id="invertY" style="width:auto"/> invert Y
      </label>
      <label style="margin:0;display:flex;align-items:center;gap:6px;cursor:pointer">
        <input type="checkbox" id="invertZ" style="width:auto"/> invert Z
      </label>
    </div>

    <h2>Presets <span class="badge" id="presetCount">—</span></h2>
    <div id="presetGrid" class="grid3"></div>
    <div style="font-size:10px;color:var(--subtext);margin-top:6px">
      <span class="key">Shift</span>+click empty = save here · <span class="key">Alt</span>+click = delete
    </div>

    <h2>Recorder <span class="badge" id="recBadge" style="display:none">REC</span></h2>
    <div class="row" style="gap:6px">
      <button id="btnRec"  style="flex:1">● REC</button>
      <button id="btnPlay" style="flex:1" disabled>▶ Play</button>
    </div>
    <select id="sceneSelect" style="margin-top:6px"><option value="">(no scenes)</option></select>
    <div class="row" style="gap:6px;margin-top:6px">
      <button id="btnSaveScene" style="flex:1">Save</button>
      <button id="btnDelScene"  style="flex:1">Delete</button>
    </div>
  </div>

  <div class="panel center">
    <h1>HELM</h1>
    <div class="stick-wrap">
      <div class="helm-wrap">
        <img id="snap" alt="" src=""/>
        <canvas id="stick" width="320" height="320"></canvas>
      </div>
      <canvas id="zoom" width="50" height="320"></canvas>
    </div>
    <div class="axes">
      <span>X</span><div class="axis-bar"><div id="axX" class="axis-fill" style="left:50%;width:0"></div></div><span id="xVal">0</span>
      <span>Y</span><div class="axis-bar"><div id="axY" class="axis-fill" style="left:50%;width:0"></div></div><span id="yVal">0</span>
      <span>Z</span><div class="axis-bar"><div id="axZ" class="axis-fill" style="left:50%;width:0"></div></div><span id="zVal">0</span>
    </div>
    <div style="margin-top:18px">
      <button class="danger" id="btnStop">PANIC STOP <span class="key">Esc</span></button>
    </div>
    <div style="font-size:10px; color: var(--subtext); margin-top: 14px; text-align:center; max-width:380px">
      Drag the stick (or touch) to pan/tilt. Drag the right strip to zoom. Release to stop.
      <br/>Server auto-stops after ~20s of no command — keep stick deflected to keep moving.
    </div>
  </div>

  <div class="panel">
    <h1>LOG</h1>
    <div id="log"></div>
    <h2>Last move command</h2>
    <pre id="lastCmd" style="font-size:10px;color:var(--subtext);background:#11111b;padding:8px;border-radius:6px;margin:0">{}</pre>
    <h2>Diagnostics</h2>
    <button onclick="testRoute()">Test /move route shape</button>
    <div style="margin-top:6px"></div>
    <button onclick="getPos()">GET position now</button>
    <div style="margin-top:6px"></div>
    <button onclick="listPresets()">Refresh presets</button>
  </div>

</div>

<div class="footer">
  © 2026 <a href="https://kccsonline.com" target="_blank">KCCS</a> — Helmsman for UniFi Protect
</div>

<script>
const log = (msg, cls='') => {
  const el = document.getElementById('log');
  const t = new Date().toLocaleTimeString();
  const div = document.createElement('div');
  div.className = cls; div.textContent = `[${t}] ${msg}`;
  el.appendChild(div); el.scrollTop = el.scrollHeight;
};

let connected = false;
let cameras = [];
let presets = [];
let savedConfig = {};

async function api(path, opts={}) {
  const r = await fetch(path, {
    method: opts.method || 'GET',
    headers: { 'Content-Type': 'application/json' },
    body: opts.body ? JSON.stringify(opts.body) : undefined
  });
  let j = null; try { j = await r.json(); } catch(e) {}
  return { ok: r.ok, status: r.status, body: j };
}

document.getElementById('btnConnect').onclick = async () => {
  const ip = document.getElementById('nvrIp').value.trim();
  const user = document.getElementById('nvrUser').value.trim();
  const pass = document.getElementById('nvrPass').value;
  if (!ip || !user || !pass) { log('Enter NVR IP, user, password', 'log-err'); return; }
  log(`Connecting to ${ip} as ${user}…`, 'log-info');
  const remember     = document.getElementById('remember').checked;
  const rememberPass = document.getElementById('rememberPass').checked;
  const r = await api('/api/connect', { method: 'POST', body: { ip, user, pass, remember, remember_password: rememberPass } });
  if (!r.ok) {
    log(`Connect failed: ${(r.body && r.body.error) || r.status}`, 'log-err');
    document.getElementById('connBadge').textContent = 'failed';
    document.getElementById('connBadge').className = 'badge bad';
    return;
  }
  connected = true;
  document.getElementById('connBadge').textContent = 'connected';
  document.getElementById('connBadge').className = 'badge ok';
  cameras = r.body.cameras;
  const ptzCount = cameras.filter(c=>c.ptz).length;
  log(`Connected. Cameras: ${cameras.length} (PTZ: ${ptzCount})`, 'log-ok');
  const sel = document.getElementById('camSelect');
  sel.innerHTML = '';
  cameras.filter(c=>c.ptz).forEach(c => {
    const o = document.createElement('option');
    o.value = c.id; o.textContent = `${c.name || '(unnamed)'} — ${c.market || c.type}`;
    sel.appendChild(o);
  });
  if (sel.options.length === 0) {
    const o = document.createElement('option'); o.textContent = '(no PTZ cameras found)';
    sel.appendChild(o);
  } else {
    sel.onchange = () => { listPresets(); getPos(); refreshCameraInfo(); };
    listPresets(); getPos(); refreshCameraInfo();
    setInterval(getPos, 2000);
  }
};

// ---------- snapshot preview (1 Hz poll behind the helm) ----------
let snapTimer = null;
let snapEnabled = false;
function refreshSnap() {
  if (!snapEnabled || !connected) return;
  const id = document.getElementById('camSelect').value;
  if (!id || id.startsWith('(')) return;
  // Cache-buster: append timestamp
  const img = document.getElementById('snap');
  const ts = Date.now();
  const probe = new Image();
  probe.onload  = () => { img.src = probe.src; img.classList.add('live'); };
  probe.onerror = () => { img.classList.remove('live'); };
  probe.src = `/api/snapshot?id=${encodeURIComponent(id)}&t=${ts}`;
}
function setSnapEnabled(on) {
  snapEnabled = on;
  const btn = document.getElementById('btnSnap');
  btn.textContent = on ? 'Hide preview' : 'Live preview';
  btn.style.background = on ? 'var(--green)' : '';
  btn.style.color      = on ? '#11111b' : '';
  if (on) {
    refreshSnap();
    snapTimer = setInterval(refreshSnap, 1000);
  } else {
    clearInterval(snapTimer); snapTimer = null;
    document.getElementById('snap').src = '';
    document.getElementById('snap').classList.remove('live');
  }
}
document.getElementById('btnSnap').onclick = () => setSnapEnabled(!snapEnabled);

// ---------- camera-side controls ----------
async function refreshCameraInfo() {
  if (!connected) return;
  const id = document.getElementById('camSelect').value;
  if (!id || id.startsWith('(')) return;
  const r = await api(`/api/camera?id=${encodeURIComponent(id)}`);
  if (!r.ok) return;
  document.getElementById('irMode').value = r.body.irLedMode || '';
  // Per-camera local axis flips (loaded from saved config)
  const cs = (savedConfig.cam_settings || {})[id] || {};
  document.getElementById('invertX').checked = !!cs.invertX;
  document.getElementById('invertY').checked = !!cs.invertY;
  document.getElementById('invertZ').checked = !!cs.invertZ;
  // Flashlight button enabled state
  document.getElementById('btnFlash').disabled = !r.body.hasFlashlight;
}

document.getElementById('btnLocate').onclick = async () => {
  const id = document.getElementById('camSelect').value;
  if (!id || id.startsWith('(')) return;
  log('-> locate', 'log-info');
  const r = await api('/api/locate', { method: 'POST', body: { id } });
  log(`<- ${r.status}`, r.ok ? 'log-ok' : 'log-err');
};
let flashOn = false;
document.getElementById('btnFlash').onclick = async () => {
  const id = document.getElementById('camSelect').value;
  if (!id || id.startsWith('(')) return;
  flashOn = !flashOn;
  log(`-> flashlight ${flashOn ? 'on' : 'off'}`, 'log-info');
  const r = await api('/api/flashlight', { method: 'POST', body: { id, enable: flashOn } });
  log(`<- ${r.status}`, r.ok ? 'log-ok' : 'log-err');
  document.getElementById('btnFlash').style.background = flashOn ? 'var(--yellow)' : '';
  document.getElementById('btnFlash').style.color      = flashOn ? '#11111b' : '';
};
document.getElementById('irMode').onchange = async (e) => {
  const id = document.getElementById('camSelect').value;
  if (!id || id.startsWith('(') || !e.target.value) return;
  log(`-> IR ${e.target.value}`, 'log-info');
  const r = await api('/api/camera_patch', { method: 'POST', body: {
    id, patch: { ispSettings: { irLedMode: e.target.value } }
  } });
  log(`<- ${r.status}`, r.ok ? 'log-ok' : 'log-err');
};
['invertX','invertY','invertZ'].forEach(name => {
  document.getElementById(name).onchange = async (e) => {
    const id = document.getElementById('camSelect').value;
    if (!id || id.startsWith('(')) return;
    const patch = {}; patch[name] = e.target.checked;
    await api('/api/cam_settings', { method: 'POST', body: { id, patch } });
    // Refresh local cache
    savedConfig = (await (await fetch('/api/config')).json());
  };
});

async function getPos() {
  if (!connected) return;
  const id = document.getElementById('camSelect').value;
  if (!id || id.startsWith('(')) return;
  const r = await api(`/api/position?id=${encodeURIComponent(id)}`);
  if (r.ok && r.body && r.body.degree) {
    const p = r.body.degree;
    document.getElementById('posDisplay').textContent =
      `pan: ${p.pan ?? '—'}° / tilt: ${p.tilt ?? '—'}° / zoom: ${typeof p.zoom === 'number' ? p.zoom.toFixed(2) + 'x' : '—'}`;
  }
}

async function listPresets() {
  if (!connected) return;
  const id = document.getElementById('camSelect').value;
  if (!id || id.startsWith('(')) return;
  const r = await api(`/api/presets?id=${encodeURIComponent(id)}`);
  presets = (r.body && r.body.presets) || [];
  document.getElementById('presetCount').textContent = `${presets.length} saved`;
  const grid = document.getElementById('presetGrid');
  grid.innerHTML = '';
  for (let i = 1; i <= 9; i++) {
    const p = presets.find(p => p.slot === i);
    const b = document.createElement('button');
    b.className = 'preset';
    b.textContent = p ? `${i}: ${(p.name || '').slice(0,9)}` : `(${i})`;
    b.disabled = false;  // always enabled for save-on-empty
    b.onclick = (ev) => {
      const id = document.getElementById('camSelect').value;
      if (ev.shiftKey) {
        const name = prompt(p ? `Rename preset ${i} to:` : `Save current position as preset ${i}. Name:`, p ? p.name : `slot${i}`);
        if (name === null) return;
        if (p) {
          api('/api/rename_preset', { method: 'POST', body: { id, slot: i, name } }).then(r => {
            log(`<- rename preset ${i}: ${r.status}`, r.ok ? 'log-ok' : 'log-err');
            listPresets();
          });
        } else {
          api('/api/save_preset', { method: 'POST', body: { id, name } }).then(r => {
            log(`<- save preset: ${r.status}`, r.ok ? 'log-ok' : 'log-err');
            listPresets();
          });
        }
      } else if (ev.altKey && p) {
        if (confirm(`Delete preset ${i} "${p.name}"?`)) {
          api('/api/delete_preset', { method: 'POST', body: { id, slot: i } }).then(r => {
            log(`<- delete preset ${i}: ${r.status}`, r.ok ? 'log-ok' : 'log-err');
            listPresets();
          });
        }
      } else if (p) {
        goPreset(i);
      }
    };
    grid.appendChild(b);
  }
}

async function goPreset(slot) {
  const id = document.getElementById('camSelect').value;
  log(`-> goto preset ${slot}`, 'log-info');
  const r = await api(`/api/preset?id=${encodeURIComponent(id)}&slot=${slot}`, { method: 'POST' });
  log(`<- ${r.status}`, r.ok ? 'log-ok' : 'log-err');
}

async function testRoute() {
  if (!connected) { log('Connect first', 'log-err'); return; }
  const id = document.getElementById('camSelect').value;
  if (!id || id.startsWith('(')) { log('Select a camera first', 'log-err'); return; }
  log('Testing /move with zero-vector continuous (no movement expected)…', 'log-info');
  const r = await api('/api/move', { method: 'POST',
    body: { id, payload: { type: 'continuous', payload: { x: 0, y: 0, z: 0 } } } });
  log(`<- ${r.status} ${JSON.stringify(r.body).slice(0,200)}`, r.ok ? 'log-ok' : 'log-err');
}

const stick = document.getElementById('stick');
const sctx = stick.getContext('2d');
const zoom = document.getElementById('zoom');
const zctx = zoom.getContext('2d');
let stX = 0, stY = 0, stZ = 0;
let sticking = false, zooming = false;

function drawStick() {
  const w = stick.width, h = stick.height;
  const cx = w/2, cy = h/2, r = w/2 - 16;
  sctx.clearRect(0,0,w,h);
  sctx.strokeStyle = '#585b70'; sctx.lineWidth = 2;
  sctx.beginPath(); sctx.arc(cx,cy,r,0,Math.PI*2); sctx.stroke();
  sctx.strokeStyle = '#313244'; sctx.lineWidth = 1;
  sctx.beginPath(); sctx.moveTo(cx-r,cy); sctx.lineTo(cx+r,cy);
  sctx.moveTo(cx,cy-r); sctx.lineTo(cx,cy+r); sctx.stroke();
  const dz = parseInt(document.getElementById('dead').value)/100 * r;
  sctx.strokeStyle = '#45475a';
  sctx.beginPath(); sctx.arc(cx,cy,dz,0,Math.PI*2); sctx.stroke();
  const kx = cx + stX * r, ky = cy + (-stY) * r;
  sctx.fillStyle = sticking ? '#89b4fa' : '#a6adc8';
  sctx.beginPath(); sctx.arc(kx,ky,18,0,Math.PI*2); sctx.fill();
}

function drawZoom() {
  const w = zoom.width, h = zoom.height;
  zctx.clearRect(0,0,w,h);
  zctx.strokeStyle = '#585b70'; zctx.strokeRect(0,0,w,h);
  zctx.strokeStyle = '#313244'; zctx.beginPath();
  zctx.moveTo(0, h/2); zctx.lineTo(w, h/2); zctx.stroke();
  const ky = h/2 + (-stZ) * (h/2 - 12);
  zctx.fillStyle = zooming ? '#f5c2e7' : '#a6adc8';
  zctx.fillRect(4, ky-10, w-8, 20);
  zctx.fillStyle = '#cdd6f4'; zctx.font = '10px monospace';
  zctx.fillText('Z+', w/2-7, 12);
  zctx.fillText('Z-', w/2-7, h-4);
}

function setStickFromEvent(e) {
  const rc = stick.getBoundingClientRect();
  const cx = rc.left + rc.width/2, cy = rc.top + rc.height/2;
  const r = rc.width/2 - 16;
  let pt = (e.touches && e.touches[0]) || e;
  let dx = (pt.clientX - cx) / r;
  let dy = (pt.clientY - cy) / r;
  const m = Math.hypot(dx, dy);
  if (m > 1) { dx /= m; dy /= m; }
  stX = dx; stY = -dy;
}
function setZoomFromEvent(e) {
  const rc = zoom.getBoundingClientRect();
  const cy = rc.top + rc.height/2;
  let pt = (e.touches && e.touches[0]) || e;
  let dy = (pt.clientY - cy) / (rc.height/2 - 12);
  if (dy > 1) dy = 1; if (dy < -1) dy = -1;
  stZ = -dy;
}
function applyDead(v) {
  const dz = parseInt(document.getElementById('dead').value)/100;
  if (Math.abs(v) < dz) return 0;
  const sign = v < 0 ? -1 : 1;
  return sign * (Math.abs(v) - dz) / (1 - dz);
}

stick.addEventListener('pointerdown', e => { sticking = true; stick.setPointerCapture(e.pointerId); setStickFromEvent(e); });
stick.addEventListener('pointermove', e => { if (sticking) setStickFromEvent(e); });
stick.addEventListener('pointerup',   e => { sticking = false; stX = 0; stY = 0; });
stick.addEventListener('pointercancel', e => { sticking = false; stX = 0; stY = 0; });
zoom.addEventListener('pointerdown', e => { zooming = true; zoom.setPointerCapture(e.pointerId); setZoomFromEvent(e); });
zoom.addEventListener('pointermove', e => { if (zooming) setZoomFromEvent(e); });
zoom.addEventListener('pointerup',   e => { zooming = false; stZ = 0; });
zoom.addEventListener('pointercancel', e => { zooming = false; stZ = 0; });

// Keyboard arrow-key nudges (relative move bumps)
document.addEventListener('keydown', e => {
  if (e.key === 'Escape') { panicStop(); return; }
  // Skip if typing in an input field
  if (e.target.tagName === 'INPUT' || e.target.tagName === 'SELECT' || e.target.tagName === 'TEXTAREA') return;
  if (!connected) return;
  const id = document.getElementById('camSelect').value;
  if (!id || id.startsWith('(')) return;
  const big = e.shiftKey;
  const step = big ? 600 : 200;
  const speed = big ? 800 : 500;
  let panPos = 0, tiltPos = 0, zoomPos = 0;
  const fx = document.getElementById('invertX').checked ? -1 : 1;
  const fy = document.getElementById('invertY').checked ? -1 : 1;
  if (e.key === 'ArrowLeft')  panPos  = -step * fx;
  else if (e.key === 'ArrowRight') panPos  =  step * fx;
  else if (e.key === 'ArrowUp')    tiltPos = -step * fy;
  else if (e.key === 'ArrowDown')  tiltPos =  step * fy;
  else if (e.key === '+' || e.key === '=') zoomPos =  step;
  else if (e.key === '-' || e.key === '_') zoomPos = -step;
  else if (e.key === 'h' || e.key === 'H') { goPreset(0); return; }
  else if ('123456789'.includes(e.key)) { goPreset(parseInt(e.key)); return; }
  else return;
  e.preventDefault();
  api('/api/move', { method: 'POST', body: { id, payload: { type: 'relative',
    payload: { panPos, tiltPos, zoomPos, panSpeed: speed, tiltSpeed: speed, zoomSpeed: speed, scale: 'normalized' } } } });
});
document.getElementById('btnStop').onclick = panicStop;
async function panicStop() {
  stX = 0; stY = 0; stZ = 0; sticking = false; zooming = false;
  log('PANIC STOP', 'log-err');
  if (!connected) return;
  const id = document.getElementById('camSelect').value;
  if (id && !id.startsWith('(')) {
    await api('/api/move', { method: 'POST',
      body: { id, payload: { type: 'continuous', payload: { x: 0, y: 0, z: 0 } } } });
  }
}

const labels = { speed: '%', dead: '%', rate: ' Hz' };
['speed','dead','rate'].forEach(id => {
  document.getElementById(id).addEventListener('input', e => {
    document.getElementById(id+'Lbl').textContent = e.target.value + labels[id];
  });
});

// ---------- USB gamepad / joystick (via browser GamepadAPI) ----------
let padIndex = null;
let padName  = '';
window.addEventListener('gamepadconnected', (e) => {
  padIndex = e.gamepad.index;
  padName  = e.gamepad.id;
  log(`Gamepad connected: ${padName}`, 'log-ok');
  const b = document.getElementById('padBadge');
  b.textContent = '🎮 ' + padName.split('(')[0].trim().slice(0,20);
  b.className = 'badge ok';
  b.style.display = '';
});
window.addEventListener('gamepaddisconnected', (e) => {
  if (e.gamepad.index === padIndex) {
    log('Gamepad disconnected', 'log-info');
    padIndex = null;
    document.getElementById('padBadge').style.display = 'none';
  }
});

let lastButtonStates = [];
function readPad() {
  // Returns {x, y, z, buttons:[bool,...], active:bool} normalized to -1..1
  if (padIndex === null) return null;
  const pads = navigator.getGamepads ? navigator.getGamepads() : [];
  const p = pads[padIndex];
  if (!p) return null;
  // Standard mapping: axes[0]=LX, axes[1]=LY (down+), axes[2]=RX, axes[3]=RY
  // triggers (LT/RT) are buttons[6] and [7] on most controllers, with .value 0..1
  const lx = p.axes[0] || 0;
  const ly = p.axes[1] || 0;
  const rt = (p.buttons[7] && p.buttons[7].value) || 0;
  const lt = (p.buttons[6] && p.buttons[6].value) || 0;
  const x = lx;
  const y = -ly;            // gamepad LY+ = down -> flip so up = +y on stick
  const z = rt - lt;        // RT zoom in, LT zoom out
  const active = Math.abs(x) > 0.05 || Math.abs(y) > 0.05 || Math.abs(z) > 0.05;
  // capture button presses (rising edge)
  const btns = p.buttons.map(b => !!b.pressed);
  const events = [];
  for (let i = 0; i < btns.length; i++) {
    if (btns[i] && !lastButtonStates[i]) events.push(i);
  }
  lastButtonStates = btns;
  return { x, y, z, active, events };
}

function handlePadButtons(events) {
  // Standard gamepad button layout (Xbox-style):
  //  0 = A      1 = B       2 = X       3 = Y
  //  4 = LB     5 = RB      6 = LT(*)   7 = RT(*)
  //  8 = Back   9 = Start  10 = LStick 11 = RStick
  // 12 = D-Up  13 = D-Down 14 = D-L    15 = D-R
  for (const i of events) {
    if (i >= 0 && i <= 3)              goPreset(i + 1);                      // A/B/X/Y -> presets 1-4
    else if (i === 12) goPreset(5);    else if (i === 13) goPreset(6);
    else if (i === 14) goPreset(7);    else if (i === 15) goPreset(8);
    else if (i === 9)  panicStop();                                          // Start = panic
    else if (i === 4 || i === 5) cycleCamera(i === 5 ? 1 : -1);              // LB/RB switch cam
  }
}

function cycleCamera(dir) {
  const sel = document.getElementById('camSelect');
  if (sel.options.length === 0 || sel.options[0].value === '') return;
  const n = sel.options.length;
  let idx = (sel.selectedIndex + dir + n) % n;
  sel.selectedIndex = idx;
  sel.dispatchEvent(new Event('change'));
  log(`-> cam ${sel.options[idx].textContent}`, 'log-info');
}

// ---------- Move recorder + scene playback ----------
let recording = false;
let recStart = 0;
let recFrames = [];        // [{t_ms, x, y, z}]
let playing = false;
let playTimers = [];

function loadScenes() {
  try { return JSON.parse(localStorage.getItem('helmsman.scenes') || '{}'); }
  catch (e) { return {}; }
}
function saveScenes(s) { localStorage.setItem('helmsman.scenes', JSON.stringify(s)); }
function refreshSceneList() {
  const scenes = loadScenes();
  const sel = document.getElementById('sceneSelect');
  const cur = sel.value;
  sel.innerHTML = '';
  const names = Object.keys(scenes).sort();
  if (names.length === 0) {
    const o = document.createElement('option'); o.value = ''; o.textContent = '(no scenes)';
    sel.appendChild(o);
    document.getElementById('btnPlay').disabled = true;
  } else {
    for (const n of names) {
      const o = document.createElement('option'); o.value = n;
      o.textContent = `${n}  (${scenes[n].length}f, ${(scenes[n][scenes[n].length-1].t/1000).toFixed(1)}s)`;
      sel.appendChild(o);
    }
    sel.value = cur && scenes[cur] ? cur : names[0];
    document.getElementById('btnPlay').disabled = false;
  }
}

document.getElementById('btnRec').onclick = () => {
  if (recording) {
    recording = false;
    document.getElementById('btnRec').textContent = '● REC';
    document.getElementById('btnRec').style.background = '';
    document.getElementById('recBadge').style.display = 'none';
    log(`Recorded ${recFrames.length} frames over ${((Date.now()-recStart)/1000).toFixed(1)}s`, 'log-ok');
  } else {
    recording = true;
    recStart = Date.now();
    recFrames = [];
    document.getElementById('btnRec').textContent = '■ Stop';
    document.getElementById('btnRec').style.background = 'var(--red)';
    document.getElementById('btnRec').style.color = '#11111b';
    document.getElementById('recBadge').style.display = '';
    log('Recording…', 'log-info');
  }
};

document.getElementById('btnSaveScene').onclick = () => {
  if (recFrames.length === 0) { log('Nothing recorded yet', 'log-err'); return; }
  const name = prompt('Scene name:', `scene-${Object.keys(loadScenes()).length+1}`);
  if (!name) return;
  const scenes = loadScenes();
  scenes[name] = recFrames.map(f => ({...f}));  // deep copy
  saveScenes(scenes);
  refreshSceneList();
  document.getElementById('sceneSelect').value = name;
  log(`Saved scene "${name}"`, 'log-ok');
};

document.getElementById('btnDelScene').onclick = () => {
  const sel = document.getElementById('sceneSelect');
  const name = sel.value;
  if (!name) return;
  if (!confirm(`Delete scene "${name}"?`)) return;
  const scenes = loadScenes(); delete scenes[name]; saveScenes(scenes);
  refreshSceneList();
  log(`Deleted scene "${name}"`, 'log-info');
};

document.getElementById('btnPlay').onclick = () => {
  if (playing) {
    playTimers.forEach(t => clearTimeout(t));
    playTimers = [];
    playing = false;
    document.getElementById('btnPlay').textContent = '▶ Play';
    panicStop();
    return;
  }
  const name = document.getElementById('sceneSelect').value;
  const scenes = loadScenes();
  const frames = scenes[name];
  if (!frames || frames.length === 0) return;
  if (!connected) { log('Not connected', 'log-err'); return; }
  const id = document.getElementById('camSelect').value;
  if (!id || id.startsWith('(')) { log('Pick a camera first', 'log-err'); return; }
  playing = true;
  document.getElementById('btnPlay').textContent = '■ Stop';
  log(`Playing "${name}" (${frames.length} frames)`, 'log-info');
  for (const f of frames) {
    playTimers.push(setTimeout(() => {
      api('/api/move', { method: 'POST', body: { id, payload: { type: 'continuous', payload: { x: f.x, y: f.y, z: f.z } } } });
    }, f.t));
  }
  // Final stop frame
  playTimers.push(setTimeout(() => {
    api('/api/move', { method: 'POST', body: { id, payload: { type: 'continuous', payload: { x: 0, y: 0, z: 0 } } } });
    playing = false;
    document.getElementById('btnPlay').textContent = '▶ Play';
    log('Playback complete', 'log-ok');
  }, frames[frames.length-1].t + 200));
};

let lastSent = { x:0, y:0, z:0 };
let lastSentTime = 0;
function tick() {
  // Gamepad input overrides on-screen stick whenever it's deflected.
  const pad = readPad();
  if (pad) {
    if (pad.events.length) handlePadButtons(pad.events);
    if (pad.active) { stX = pad.x; stY = pad.y; stZ = pad.z; }
  }
  const xa = applyDead(stX), ya = applyDead(stY), za = applyDead(stZ);
  const sp = parseInt(document.getElementById('speed').value)/100;
  const x = xa * sp, y = ya * sp, z = za * sp;
  const setBar = (id, v) => {
    const e = document.getElementById(id);
    if (v >= 0) { e.style.left = '50%'; e.style.width = (v*50)+'%'; }
    else        { e.style.left = (50 + v*50)+'%'; e.style.width = (-v*50)+'%'; }
  };
  setBar('axX', x); setBar('axY', y); setBar('axZ', z);

  // Continuous coords are -1000..1000.
  // Convention: stick up = camera looks up in the live image.
  const fx = document.getElementById('invertX').checked ? -1 : 1;
  const fy = document.getElementById('invertY').checked ? -1 : 1;
  const fz = document.getElementById('invertZ').checked ? -1 : 1;
  const sx = Math.round(x * 1000 * fx);
  const sy = Math.round(y * 1000 * fy);
  const sz = Math.round(z * 1000 * fz);
  document.getElementById('xVal').textContent = sx;
  document.getElementById('yVal').textContent = sy;
  document.getElementById('zVal').textContent = sz;
  drawStick(); drawZoom();

  if (!connected) return;
  const id = document.getElementById('camSelect').value;
  if (!id || id.startsWith('(')) return;

  const isMoving = (sx !== 0 || sy !== 0 || sz !== 0);
  const wasMoving = (lastSent.x !== 0 || lastSent.y !== 0 || lastSent.z !== 0);
  const now = performance.now();
  const rateHz = parseInt(document.getElementById('rate').value);
  const interval = 1000 / rateHz;

  if (isMoving && (now - lastSentTime) >= interval) {
    const payload = { type: 'continuous', payload: { x: sx, y: sy, z: sz } };
    document.getElementById('lastCmd').textContent = JSON.stringify(payload, null, 2);
    api('/api/move', { method: 'POST', body: { id, payload } });
    lastSent = { x: sx, y: sy, z: sz };
    lastSentTime = now;
    if (recording) recFrames.push({ t: Date.now() - recStart, x: sx, y: sy, z: sz });
  } else if (!isMoving && wasMoving) {
    const payload = { type: 'continuous', payload: { x: 0, y: 0, z: 0 } };
    document.getElementById('lastCmd').textContent = JSON.stringify(payload, null, 2);
    api('/api/move', { method: 'POST', body: { id, payload } });
    lastSent = { x: 0, y: 0, z: 0 };
    lastSentTime = now;
    if (recording) recFrames.push({ t: Date.now() - recStart, x: 0, y: 0, z: 0 });
  }
}
setInterval(tick, 30);
drawStick(); drawZoom();

// Prefill from server defaults (env vars + saved config)
async function refreshConfig() {
  const c = await (await fetch('/api/config')).json();
  savedConfig = c;
  if (c.nvr)  document.getElementById('nvrIp').value   = c.nvr;
  if (c.user) document.getElementById('nvrUser').value = c.user;
  if (c.pass) document.getElementById('nvrPass').value = c.pass;
  document.getElementById('rememberPass').checked = !!c.pass;
  document.getElementById('forgetLink').style.display = c.saved ? '' : 'none';
}
refreshConfig();
refreshSceneList();

document.getElementById('forgetLink').addEventListener('click', async (e) => {
  e.preventDefault();
  if (!confirm('Forget saved NVR credentials?')) return;
  await fetch('/api/forget', { method: 'POST' });
  document.getElementById('nvrIp').value = '';
  document.getElementById('nvrUser').value = '';
  document.getElementById('nvrPass').value = '';
  document.getElementById('rememberPass').checked = false;
  document.getElementById('forgetLink').style.display = 'none';
  log('Saved credentials forgotten', 'log-info');
});

// Auto-connect if we have saved creds (incl. password)
window.addEventListener('load', () => {
  setTimeout(async () => {
    const c = await (await fetch('/api/config')).json();
    if (c.nvr && c.user && c.pass) {
      log('Auto-connecting from saved credentials…', 'log-info');
      document.getElementById('btnConnect').click();
    }
  }, 250);
});

window.addEventListener('beforeunload', panicStop);
</script>
</body></html>
"""


class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a, **kw): pass

    def _json(self, code, obj):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(body)

    def _read(self):
        n = int(self.headers.get("Content-Length", "0") or 0)
        if not n: return {}
        try:
            return json.loads(self.rfile.read(n).decode("utf-8"))
        except Exception:
            return {}

    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        qs = urllib.parse.parse_qs(u.query)
        if u.path in ("/", "/index.html"):
            body = HTML.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if u.path == "/api/config":
            d = effective_defaults()
            return self._json(200, {
                "nvr": d["ip"], "user": d["user"], "pass": d["pass"],
                "saved": d["saved"], "cam_settings": d["cam_settings"],
            })
        if u.path == "/api/position":
            cam = qs.get("id", [""])[0]
            code, j, txt = nvr.position(cam)
            if code == 200 and j is not None:
                return self._json(200, j)
            return self._json(code, {"error": txt[:300]})
        if u.path == "/api/presets":
            cam = qs.get("id", [""])[0]
            code, j, txt = nvr.list_presets(cam)
            if code == 200 and j is not None:
                arr = j if isinstance(j, list) else j.get("presets", [])
                presets = [{"slot": p.get("slot", i+1), "name": p.get("name", f"slot{i+1}")} for i, p in enumerate(arr)]
                return self._json(200, {"presets": presets})
            return self._json(code, {"error": txt[:300], "presets": []})
        if u.path == "/api/snapshot":
            if not nvr.connected:
                return self._json(401, {"error": "not connected"})
            cam = qs.get("id", [""])[0]
            code, ctype, blob = nvr.snapshot(cam, hires=qs.get("hi", ["0"])[0] == "1")
            if code == 200 and blob:
                self.send_response(200)
                self.send_header("Content-Type", ctype or "image/jpeg")
                self.send_header("Content-Length", str(len(blob)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(blob)
                return
            return self._json(code or 502, {"error": "snapshot failed"})
        if u.path == "/api/camera":
            if not nvr.connected:
                return self._json(401, {"error": "not connected"})
            cam = qs.get("id", [""])[0]
            code, j, txt = nvr.get_camera(cam)
            if code == 200 and j is not None:
                isp = j.get("ispSettings") or {}
                fl  = (j.get("featureFlags") or {})
                led = (j.get("ledSettings") or {})
                return self._json(200, {
                    "id": j.get("id"),
                    "name": j.get("name"),
                    "isFlippedHorizontal": isp.get("isFlippedHorizontal", False),
                    "isFlippedVertical":   isp.get("isFlippedVertical", False),
                    "irLedMode": isp.get("irLedMode", ""),  # "auto" | "on" | "off" | "autoFilterOnly"
                    "ledStatus": led.get("isEnabled", False),
                    "hasFlashlight": bool(fl.get("hasFlashlight") or fl.get("hasSpotlight")),
                    "hasSpeaker":    bool(fl.get("hasSpeaker") or fl.get("speakerSettings")),
                    "hasMic":        bool(fl.get("hasMic") or fl.get("mic")),
                })
            return self._json(code, {"error": txt[:300]})
        self._json(404, {"error": "not found"})

    def do_POST(self):
        u = urllib.parse.urlparse(self.path)
        qs = urllib.parse.parse_qs(u.query)
        body = self._read()
        if u.path == "/api/forget":
            forget_config()
            return self._json(200, {"ok": True})
        if u.path == "/api/connect":
            ip   = (body.get("ip") or "").strip()
            user = (body.get("user") or "").strip()
            pw   = body.get("pass") or ""
            remember     = bool(body.get("remember"))
            remember_pw  = bool(body.get("remember_password"))
            if not (ip and user and pw):
                return self._json(400, {"error": "ip, user, pass all required"})
            global nvr
            nvr = Nvr(ip)
            ok, msg = nvr.login(user, pw)
            if not ok:
                return self._json(401, {"error": msg})
            code, b, txt = nvr.bootstrap()
            if code != 200 or not b:
                return self._json(code, {"error": txt[:300]})
            if remember:
                cfg = {
                    "ip": ip,
                    "user": user,
                    "save_password": bool(remember_pw),
                    "cam_settings": load_config().get("cam_settings", {}),
                }
                if remember_pw:
                    backend = store_password(user, pw)
                    if backend == "file":
                        cfg["pass"] = pw
                save_config(cfg)
            telegram_notify(f"Helmsman session started: {user}@{ip} ({len(b.get('cameras',[]))} cams)")
            cams = []
            for c in b.get("cameras", []):
                ff = c.get("featureFlags") or {}
                ptz = bool(ff.get("isPtz") or c.get("ptzControlEnabled") or c.get("ptz"))
                cams.append({
                    "id":     c.get("id"),
                    "name":   (c.get("name") or "").strip(),
                    "type":   c.get("type"),
                    "market": c.get("marketName"),
                    "state":  c.get("state"),
                    "ptz":    ptz,
                })
            return self._json(200, {"cameras": cams})
        if u.path == "/api/move":
            if not nvr.connected:
                return self._json(401, {"error": "not connected"})
            cam = body.get("id")
            payload = body.get("payload") or {}
            code, txt = nvr.move(cam, payload)
            try:
                j = json.loads(txt)
            except Exception:
                j = {"raw": txt[:300]}
            # First non-zero move of the session triggers a Telegram notification (rate-limited).
            try:
                inner = (payload or {}).get("payload") or {}
                if any(abs(inner.get(k, 0)) > 50 for k in ("x", "y", "z")):
                    telegram_notify(f"Helmsman: PTZ command sent (cam={cam})")
            except Exception:
                pass
            return self._json(code, j)
        if u.path == "/api/save_preset":
            if not nvr.connected:
                return self._json(401, {"error": "not connected"})
            cam  = body.get("id")
            name = (body.get("name") or "").strip()
            code, txt = nvr.save_preset(cam, name)
            try: j = json.loads(txt)
            except: j = {"raw": txt[:300]}
            return self._json(code, j)
        if u.path == "/api/rename_preset":
            if not nvr.connected:
                return self._json(401, {"error": "not connected"})
            cam  = body.get("id")
            slot = int(body.get("slot", 1))
            name = (body.get("name") or "").strip()
            code, txt = nvr.rename_preset(cam, slot, name)
            return self._json(code, {"raw": txt[:300]})
        if u.path == "/api/delete_preset":
            if not nvr.connected:
                return self._json(401, {"error": "not connected"})
            cam  = body.get("id")
            slot = int(body.get("slot", 1))
            code, txt = nvr.delete_preset(cam, slot)
            return self._json(code, {"raw": txt[:300]})
        if u.path == "/api/locate":
            if not nvr.connected:
                return self._json(401, {"error": "not connected"})
            code, txt = nvr.locate(body.get("id"))
            return self._json(code, {"raw": txt[:300]})
        if u.path == "/api/flashlight":
            if not nvr.connected:
                return self._json(401, {"error": "not connected"})
            code, txt = nvr.flashlight(body.get("id"), bool(body.get("enable")))
            return self._json(code, {"raw": txt[:300]})
        if u.path == "/api/camera_patch":
            if not nvr.connected:
                return self._json(401, {"error": "not connected"})
            cam   = body.get("id")
            patch = body.get("patch") or {}
            code, txt = nvr.patch_camera(cam, patch)
            try: j = json.loads(txt)
            except: j = {"raw": txt[:300]}
            return self._json(code, j)
        if u.path == "/api/cam_settings":
            cam = body.get("id")
            patch = body.get("patch") or {}
            cur = update_cam_settings(cam, patch)
            return self._json(200, {"settings": cur})
        if u.path == "/api/preset":
            if not nvr.connected:
                return self._json(401, {"error": "not connected"})
            cam = qs.get("id", [""])[0]
            try:
                slot = int(qs.get("slot", ["1"])[0])
            except ValueError:
                return self._json(400, {"error": "bad slot"})
            code, txt = nvr.goto_preset(cam, slot)
            try:
                j = json.loads(txt)
            except Exception:
                j = {"raw": txt[:300]}
            return self._json(code, j)
        self._json(404, {"error": "not found"})


class ReuseTCPServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def main():
    print("Helmsman — UniFi Protect virtual joystick PTZ controller")
    print(f"  Listening on http://{BIND}:{PORT}")
    print(f"  Open in your browser, fill in NVR IP / user / password, click Connect.")
    if DEFAULT_NVR_IP:
        print(f"  (Pre-fill from env: HELMSMAN_NVR_IP={DEFAULT_NVR_IP})")
    httpd = ReuseTCPServer((BIND, PORT), Handler)
    threading.Timer(0.5, lambda: webbrowser.open(f"http://{'127.0.0.1' if BIND in ('0.0.0.0', BIND) else BIND}:{PORT}/")).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nshutting down")
        httpd.shutdown()


if __name__ == "__main__":
    main()
