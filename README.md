# Helmsman

A virtual joystick PTZ controller for **UniFi Protect**. Drag the on-screen stick, your camera moves. Built because Ubiquiti doesn't ship a joystick mode.

Works with G5 PTZ (and any Protect-adopted camera that has continuous-move support — `featureFlags.isPtz: true` in the bootstrap).

## Run

```
pip install requests
python helmsman.py
```

Browser opens to `http://127.0.0.1:8765`. Type your NVR IP, your local Protect username, and password. Click **Connect & Bootstrap**.

Or double-click `run.bat` on Windows.

## Optional environment defaults

```
HELMSMAN_NVR_IP=192.168.1.50
HELMSMAN_USER=localadmin
HELMSMAN_PASS=…
HELMSMAN_PORT=8765        (default)
HELMSMAN_BIND=127.0.0.1   (set 0.0.0.0 to expose on LAN)
```

If set, the UI prefills these. **You can leave them all unset and just type creds in the UI** — they're never written to disk.

## What it does

```
[browser stick]  --POST /api/move-->  [local Python]  --HTTPS POST--> [NVR /proxy/protect/api/cameras/:id/move]
                                       (session cookie + CSRF)              {type:"continuous", payload:{x,y,z}}
```

- Drag the round stick → continuous pan/tilt
- Drag the right vertical strip → continuous zoom
- Release → camera stops (one zero-vector sent)
- 5–20 Hz repeat rate while the stick is off-center (slider)
- Server-side dead-man timer (~20s in Protect 7.1) auto-stops the camera if the bridge dies
- `Esc` or red **PANIC STOP** button → immediate zero
- Live position display, preset 1-9 buttons (auto-populated from camera presets)
- `Test /move route shape` button — sends zero-vector to confirm the route accepts the payload, doesn't move the camera

## Safety controls
- **Speed limit** slider — clips output, useful while you tune feel
- **Deadzone** slider — stick noise floor (default 8%)
- **Send rate** — how often to repost while the stick is held (default 10 Hz)

## Verified API surface (Protect 7.1.46)

| Method | Path | Body |
|--------|------|------|
| `POST` | `/api/auth/login` | `{username, password, rememberMe:false}` → `TOKEN` cookie + `X-CSRF-Token` header |
| `GET`  | `/proxy/protect/api/bootstrap` | camera inventory |
| `GET`  | `/proxy/protect/api/cameras/{id}/ptz/position` | `{degree:{pan,tilt,zoom}, steps:{pan,tilt,zoom,focus}}` |
| `POST` | `/proxy/protect/api/cameras/{id}/move` | `{type, payload}` (see below) |
| `POST` | `/proxy/protect/api/cameras/{id}/ptz/goto/{slot}` | preset jump |
| `GET`  | `/proxy/protect/api/cameras/{id}/ptz/preset` | list presets |

## Move payload (discriminated union on `type`)

```json5
// continuous — joystick velocity. x/y/z in [-1000, 1000]
{ "type": "continuous", "payload": { "x": 500, "y": 0, "z": 0 } }

// relative — offset from current pan/tilt, with explicit speed
{ "type": "relative",
  "payload": { "panPos": -2000, "tiltPos": 0, "panSpeed": 500, "tiltSpeed": 500, "scale": "normalized" } }

// zoom — explicit zoom target
{ "type": "zoom",
  "payload": { "zoomPos": 730, "zoomSpeed": 500, "scale": "normalized" } }

// center — viewport-relative click target. x/y/z in [0, 1000]; 500 = center, 0 = up/left, 1000 = down/right
{ "type": "center", "payload": { "x": 800, "y": 500 } }

// preset — jump to saved slot
{ "type": "preset", "payload": { "slot": 1 } }
```

Axis conventions used by the UI:
- `x` positive → pan right
- `y` positive → tilt down (UI inverts so up-on-stick = tilt up)
- `z` positive → zoom in

## Heads-up
- **Lockout** — Protect locks the SSO account after ~5 bad logins. Auto-unlocks in ~10–15 min. Don't loop on bad creds.
- **Motor wear** — Ubiquiti's own docs note that the G5 PTZ motors aren't rated for sustained continuous operation. This is fine for human-driven joystick use; don't build it into an auto-patrol with this tool.
- **Two controllers fighting** — if you're driving the joystick and someone clicks pan in the Protect UI at the same time, the latest command wins. Don't.
- **Firmware updates** — endpoints could change in future Protect versions. The discovery logic in `helmsman.py` is straightforward; if Ubiquiti renames `/move`, grep your NVR's `/usr/share/unifi-protect/app/service.js` for `ptzMovePayloadSchema` to find the new route.

## How this came to be

Built in one session by reverse-engineering the Protect Node.js bundle on a UNVR (read-only — no NVR mods). The continuous-move endpoint is internal-but-stable: the Protect web UI uses the same surface for click-and-drag PTZ control. We're inside the supported envelope, not off it.

## License

Proprietary — © 2026 KCCS. All rights reserved.

Built by [KCCS](https://kccsonline.com).
