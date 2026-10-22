# Cloud wave 2 report: helmsman

Branch: `claude/cloud-wave2-2026-10-22` (base `faa6f36`, v0.3). No PR opened, nothing merged, tagged or released.

## TL;DR

1. **Any web page you visited could drive your cameras.** The local UI had no CSRF or Host checks. A page you visit could POST to `127.0.0.1:8765/api/move`, `/api/forget` or `/api/camera_patch`, with no response needed. With DNS rebinding it could also read `/api/config`, which returns the saved NVR password. Both are now blocked: POSTs must come from the same origin, unexpected `Host` names get a 403, and the page can't be framed.
2. **"Forget" never removed the keyring password.** It deleted `config.json` first and then read that file to find the user. Fixed.
3. **The camera could keep moving after you let go.** Releasing a USB gamepad stick could latch the last sampled deflection. Switching cameras mid-move never stopped the old camera, which drifted until Protect's ~20 s dead-man timer. Both fixed.
4. **Errors no longer drop connections.** NVR timeouts, a bad preset slot, or polling before connecting used to raise inside the handler and kill the socket. These now return JSON 4xx/5xx errors. Inputs used to build NVR URLs (camera id, NVR address, slot) are validated, so `../` can't reach other Protect endpoints.
5. **Tests and CI added.** There are 110 pytest tests (fake NVR, real local server), a ruff config, and GitHub Actions on Linux and Windows with Python 3.10 and 3.13. Everything passes locally. A headless-Chromium smoke test of the real UI against a fake NVR also passed.

No secrets, real IPs or internal hostnames were found in the tree, the full git history or the screenshots. Docs use `192.168.1.50` and the screenshots use `nvr.example.lan`.

## Commits

| Hash | What |
|---|---|
| `48f8f71` | Fix crash paths, keyring leak and stuck-camera bugs; add CSRF/DNS-rebinding guards and input validation |
| `d6d3bec` | Add pytest suite and ruff config (`pyproject.toml`) |
| `44376ec` | Add `.github/workflows/ci.yml`; `requirements-dev.txt`; raise `requests` floor to 2.32.4 |
| `4a50fa7` | Add `ARCHITECTURE.md`; correct README/USERGUIDE/CHANGELOG |
| (this commit) | This report |

## Bugs found and fixed

Line numbers refer to the original `helmsman.py` at `faa6f36`.

| # | Where | Symptom | Fix |
|---|---|---|---|
| 1 | `forget_config` L92–104 | Unlinks the config, then calls `load_config()` to find the user. That returns `{}`, so the keyring password was never deleted. | Read the user first, then delete. Test: `test_forget_config_deletes_keyring_password`. |
| 2 | `save_config` L82–89 | Wrote the file and then chmodded it, leaving a window where the plaintext password was readable at the default umask. A crash mid-write left corrupt JSON, which `load_config` silently treats as `{}`, so the next save wiped the creds. | Temp file created `0600`, then `os.replace`. Tests cover permissions and failure. |
| 3 | `update_cam_settings` L146, `/api/connect` L1257 | Unsynchronised read-modify-write from threaded handlers could lose settings. The patch also accepted arbitrary keys. | `_config_lock`; only `invertX/Y/Z` accepted, coerced to bool. A concurrency test covers it. |
| 4 | `do_GET /api/position`, `/api/presets` L1183–1196 | No `nvr.connected` check. Before connecting, `Nvr("")` requested `https:///…` and raised. This happens on every page load when a poll fires early. | Return 401 when not connected. |
| 5 | `Handler` (all NVR routes) | Any `requests` exception (NVR offline, timeout) went unhandled and dropped the socket. The UI's `fetch` then rejected with an unhandled promise rejection. | `_dispatch` wraps every route and returns 502/500 JSON. `api()` in the JS also catches network errors. |
| 6 | `/api/rename_preset`, `/api/delete_preset` L1314, L1322 | `int(body.get("slot"))` on junk raised (same socket drop). | `_parse_slot` returns 400. |
| 7 | `/api/connect` L1254–1256 | A bootstrap that returned 200 with a non-JSON body (e.g. an HTML login page) went back to the UI as **200** `{"error":…}`. The UI treated it as success and crashed on `r.body.cameras`. | Return 502. |
| 8 | `/api/connect` L1249–1250 | The global `nvr` was replaced before login succeeded, so a failed reconnect killed a working session. Old `requests.Session` objects were never closed. | Swap only after login and bootstrap succeed; close the old session. |
| 9 | `main` L1378 | `'127.0.0.1' if BIND in ('0.0.0.0', BIND) else BIND` is always true, so with `HELMSMAN_BIND=192.168.x.y` the browser opened `127.0.0.1`, where nothing listens. | `browser_url()`, tested. |
| 10 | JS `tick()` L1047–1051 | Gamepad values were copied into the stick only while the pad was "active" and never cleared on release. A fast release could latch a non-zero deflection, so the camera kept moving. | `padDriving` flag zeroes the axes on release or disconnect. |
| 11 | JS `tick()` L1076–1099 | Switching cameras (dropdown, or LB/RB on a gamepad) while moving sent the release-stop to the *new* camera. The old one kept moving for ~20 s. | Track `lastSentCam` and send a stop to it on switch. |
| 12 | JS connect handler L579 | Every reconnect added another `setInterval(getPos, 2000)`. | Single `posTimer`. |
| 13 | JS keydown L838 | `H` (home) called `goPreset(0)`, but Protect's home slot is `-1` (`PTZ_PRESET_HOME_SLOT`, per `docs/API-REFERENCE.md`). | `goPreset(-1)`. |
| 14 | JS keydown L836–837 | Keyboard zoom ignored the per-camera *invert Z* setting, while the stick honoured it. | Applies `fz`. |
| 15 | JS `beforeunload` L1140 | The async panic-stop fetch is usually cancelled when the tab closes. | `fetch(..., {keepalive:true})`. |
| 16 | JS `btnRec` L970–975 | After stopping a recording, the button kept its dark text colour on the default background. | Reset `style.color`. |
| 17 | JS `refreshSceneList` L953–961 | An empty or corrupt scene in localStorage threw on `scenes[n][len-1].t` and broke the recorder panel. | Filter out non-array and empty scenes. |
| 18 | UI hint L464 and README | "Shift+click empty = save **here**" is wrong: `POST /ptz/preset` creates at the next free slot Protect picks. | Text and prompt now say "save new preset"; docs say the same. |

## Security findings

### Fixed

- **CSRF on every POST** (`do_POST`, L1234). Now `origin_allowed()` requires the browser `Origin` to match `Host`, or a name in `HELMSMAN_ALLOWED_HOSTS`. Requests without an `Origin` header (curl, scripts) are still allowed.
- **DNS rebinding.** An attacker hostname resolving to 127.0.0.1 could read `/api/config` (saved password) and drive cameras. Now `host_allowed()` accepts only IP literals, `localhost`, or `HELMSMAN_ALLOWED_HOSTS`.
- **Clickjacking.** The page now sends `X-Frame-Options: DENY` and `frame-ancestors 'none'`.
- **Path injection into NVR URLs.** `cam_id` went straight into `/proxy/protect/api/cameras/{cam_id}/…`, so `../users/…` with PATCH via `/api/camera_patch` could reach other Protect objects. `valid_cam_id` (`[A-Za-z0-9_-]{1,64}`) now runs on every route.
- **Arbitrary camera PATCH.** `/api/camera_patch` forwarded any body to `PATCH /cameras/{id}`, which could disable recording, change privacy zones, and so on. It now accepts only `{"ispSettings":{"irLedMode": auto|on|off|autoFilterOnly}}`, which is all the UI sends. **This is a behaviour change for anyone scripting that endpoint.**
- **NVR address injection.** The `ip` field could contain `host/path?`, `user@host`, and similar. It is now restricted to host, IPv4 or `[IPv6]` with an optional `:port`. Connecting to any host is still by design (it's the user's NVR).
- **Body size.** `Content-Length` is capped at 1 MiB; JSON bodies must be objects.
- **Snapshot proxy content type.** Forced to `image/*` with `nosniff`, so the NVR can't make the local origin serve HTML.
- **Dependencies.** `requests>=2.31` allowed versions affected by CVE-2024-35195 (`verify=False` persisting on a Session) and CVE-2024-47081 (.netrc credential leak). The floor is now `>=2.32.4`. `keyring>=24` has no known advisories.

### Not fixed (design decisions)

- **The local UI has no login.** Any local process, or anyone on the LAN when `HELMSMAN_BIND=0.0.0.0`, can drive cameras and read `/api/config`, including the saved password. It's on the roadmap already. I added a startup warning when binding beyond loopback.
- **`/api/config` returns the saved password to the browser** so it can prefill the field and auto-connect. A cleaner design is server-side auto-connect, so the password never leaves the process. That changes the UI flow, so I left it.
- **TLS to the NVR isn't verified** (`verify=False`, and `urllib3.disable_warnings()` is global). UniFi consoles use self-signed certs. Options: certificate pinning (trust-on-first-use fingerprint stored in `config.json`) or an `HELMSMAN_NVR_CA` path. Until then, a MITM on the LAN can capture the NVR admin password at login. Now documented in the user guide's security section.
- **Plaintext password fallback** in `config.json` when no keyring backend exists. It's now `0600` and atomic, and documented, but still plaintext.
- **"Remember" unchecked doesn't clear an existing saved config.** That was the existing behaviour; the "forget" link does it. Left as is.
- **The recorder replays any frames in localStorage.** It's same-origin only, so no fix needed.

## Tests

```bash
pip install -r requirements-dev.txt
ruff check .
python -m pytest -q
```

- **110 tests, all passing** on Python 3.13.16 and 3.10.20, locally on Linux, in about 1 s. Ruff passes.
- `tests/test_config.py` (13): persistence, `0600`, atomic failure, forget/keyring, env precedence, concurrency.
- `tests/test_validation.py` (52 incl. parametrised): cam id, NVR host, Host/Origin guards, `browser_url`, slot parsing.
- `tests/test_http.py` (32): real `Handler` on an ephemeral port with `FakeNvr`. Covers the guards, every route, error paths and regressions.
- `tests/test_nvr.py` (10): exact URLs, headers and bodies sent to Protect; Telegram gating and throttle.
- `tests/test_ui.py` (3): `node --check` on the embedded JS; home slot; no `innerHTML` with data.
- **Manual:** headless Chromium drove the real page against the fake NVR. Connect worked; a stick drag gave 6 moves ending in a zero vector; `H` sent `goto -1`; invert-X persisted; no page errors.
- **CI:** `.github/workflows/ci.yml` runs on push and PR, read-only permissions, no secrets. I couldn't watch the first Actions run from here; the Windows leg is the one I'm least sure of (`test_save_config_is_private_from_creation` is skipped on Windows).

## Needs Justin

1. **Hardware checks on a real G5 PTZ:**
   - **`H` key / home.** Confirm `POST /ptz/goto/-1` goes home. The old code sent `0`. I followed `PTZ_PRESET_HOME_SLOT = -1` from the API reference but couldn't test it. If `-1` fails, the alternative is the `{"type":"preset","payload":{"slot":-1}}` move.
   - **Keyboard nudges.** The `relative` payload the UI sends includes `zoomPos` and `zoomSpeed`, which aren't in the documented `relative` schema. If Protect's Zod schema is strict, arrow keys and `+/-` may return 400. Press an arrow key and watch the log.
   - **Gamepad.** Push the stick hard and let it snap back. The camera should stop, and LB/RB while moving should stop the previous camera.
   - **Reverse proxy or phone by hostname.** Those users now need `HELMSMAN_ALLOWED_HOSTS=<name>`, or they'll get `403 host not allowed`. Access by IP keeps working.
2. **Decisions:**
   - Local UI auth (token in the URL printed at startup?)
   - NVR TLS pinning
   - Stop sending the saved password to the browser
3. **Credentials to rotate:** none. No committed secrets were found in the tree or history.
4. **Before the next `.exe` release:** rebuild with `requests>=2.32.4` (`pip install -U requests`), check the first CI run is green, then cut the release. I didn't tag or release.

## Unsure about

- Whether Protect accepts slot `-1` on `/ptz/goto/{slot}` (see above).
- Whether Chrome reliably delivers the `keepalive` stop on tab close when the bridge is slow. It's best-effort; Protect's 20 s timer is still the backstop.
- The Origin check allows requests with **no** `Origin` header (non-browser clients). Modern browsers always send `Origin` on cross-origin POSTs, so this keeps curl and scripts working without reopening CSRF. Very old browsers aren't covered.
