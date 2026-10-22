# Helmsman architecture

## What it is

Helmsman is a single-process desktop helper that adds a joystick mode to UniFi Protect PTZ cameras. It runs a small HTTP server on your machine, serves a browser UI with a virtual stick, and relays each stick movement to the NVR's internal `/cameras/{id}/move` API.

There is no database, no build step for the UI, and no framework. Everything lives in `helmsman.py`, which uses the standard library plus `requests` and an optional `keyring`.

## How it runs

```
python helmsman.py            # or Helmsman.exe (PyInstaller one-file build)
  └─ ReuseTCPServer(BIND, PORT)  default 127.0.0.1:8765, one thread per request
  └─ opens the default browser at browser_url(BIND, PORT)
```

Configuration comes from environment variables (all optional):

| Var | Purpose |
|---|---|
| `HELMSMAN_NVR_IP` / `_USER` / `_PASS` | Pre-fill the connect form (they override saved config) |
| `HELMSMAN_PORT`, `HELMSMAN_BIND` | Where the local UI listens |
| `HELMSMAN_ALLOWED_HOSTS` | Extra `Host` names accepted (reverse proxy or LAN hostname) |
| `HELMSMAN_CONFIG_DIR` | Location of `config.json` (default `~/.helmsman`) |
| `HELMSMAN_TELEGRAM_BOT` / `_CHAT` | Optional Telegram notifications |

## Modules (sections of `helmsman.py`)

| Section | Responsibility |
|---|---|
| Config helpers: `load_config`, `save_config`, `forget_config`, `store_password`, `fetch_password`, `effective_defaults`, `update_cam_settings` | Persist NVR, user and per-camera invert flags in `config.json`. Writes are atomic, the file is `0600`, and access is serialised by `_config_lock`. Passwords go to the OS keyring when available, otherwise into `config.json` as plaintext. |
| Validation and guards: `valid_cam_id`, `valid_nvr_host`, `host_allowed`, `origin_allowed`, `_parse_slot` | Keep user input from changing the NVR URL path. Block DNS rebinding (Host allowlist) and CSRF (Origin must match Host on POST). |
| `Nvr` | Thin `requests.Session` wrapper around the Protect API. It logs in to `/api/auth/login`, keeps the `TOKEN` cookie and `X-CSRF-Token`, then calls bootstrap, position, move, presets, snapshot, locate, flashlight and camera PATCH. TLS verification is **off** because consoles use self-signed certs. |
| `telegram_notify` | Fire-and-forget notification, throttled to once per 60 s. |
| `HTML` | The entire UI (CSS + JS) as one string. |
| `Handler` | Routes `/` and `/api/*`. `_dispatch` runs the guards, then the route, and turns exceptions into JSON 5xx responses. |
| `main` / `browser_url` | Start the server and open the browser. |

A process-global `nvr` holds the current session. `/api/connect` builds a new `Nvr`, and only after login and bootstrap succeed does it swap the new one in and close the old one.

## Data flow

```
 browser UI (HTML/JS)                      helmsman.py                         UniFi console
 ───────────────────                       ───────────                         ─────────────
 tick() every 30 ms                         Handler._post('/api/move')
  stick/gamepad → deadzone → speed →        guard: Host + Origin
  invert flags → x,y,z ∈ [-1000,1000]       validate cam id
  send at ≤ rate Hz while moving,  ──POST──▶ nvr.move(cam, payload) ──HTTPS──▶ POST /proxy/protect/api/
  one zero-vector on release                                                    cameras/{id}/move
                                                                               (server dead-man ≈20 s)
 getPos() every 2 s           ──GET───▶ /api/position  ──▶ /cameras/{id}/ptz/position
 refreshSnap() 1 Hz (opt-in)  ──GET───▶ /api/snapshot  ──▶ /cameras/{id}/snapshot
 preset buttons / keys 1-9,H  ──POST──▶ /api/preset    ──▶ /cameras/{id}/ptz/goto/{slot}
 Shift/Alt-click presets      ──POST──▶ save/rename/delete_preset ──▶ /ptz/preset[/{slot}]
 IR select                    ──POST──▶ /api/camera_patch (irLedMode only) ──▶ PATCH /cameras/{id}
 invert checkboxes            ──POST──▶ /api/cam_settings ──▶ config.json (local only)
 recorder                      frames kept in browser localStorage ("helmsman.scenes"); replayed through /api/move
```

Safety layers for the stick:

1. The UI sends a zero vector on release, on panic (Esc, gamepad Start, button), when you switch cameras mid-move, and on page unload (`fetch` keepalive).
2. Protect itself stops a continuous move after about 20 s without a new command.

## Tests

`tests/` uses pytest. `conftest.py` swaps `Nvr` for an in-memory `FakeNvr`, points config at a temp dir, fakes the keyring, and starts the real `Handler` on an ephemeral port. `test_nvr.py` checks the exact URLs, headers and bodies `Nvr` sends, using a stubbed session. `test_ui.py` runs `node --check` on the embedded script when Node is available.
