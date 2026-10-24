# UniFi Protect PTZ API — Reverse-engineered reference

This is what we learned from spelunking through the Protect Node.js bundle on a UNVR running **Protect 7.1.46**. Useful if you want to extend Helmsman, write your own client, or port this to another PTZ system.

> ⚠️ **Internal API.** Ubiquiti does not document or commit to stability of these endpoints. They've been stable across Protect 7.x so far, but a future major version could change them. The discovery technique at the bottom of this doc still applies.

## Auth

```
POST /api/auth/login
Body: {"username": "…", "password": "…", "rememberMe": false}
```

Response:
- **200** → `Set-Cookie: TOKEN=…` and response header `X-CSRF-Token: …`
- **403** with `code: AUTHENTICATION_FAILED_INVALID_CREDENTIALS` — wrong creds
- **403** with `code: AUTHENTICATION_FAILED_ACCOUNT_LOCKED` — too many failures, ~15 min lockout

Subsequent requests need:
- The `TOKEN` cookie (your HTTP client should auto-handle it)
- `X-CSRF-Token: <value from login response>` header

## Camera inventory

```
GET /proxy/protect/api/bootstrap
```

Returns the entire NVR state including `cameras: [...]`. PTZ-capable cameras have:
- `featureFlags.isPtz: true`
- `featureFlags.pan = { steps: {min, max, step}, degrees: {min, max, step} }`
- `featureFlags.tilt = { … }`
- `featureFlags.zoom = { ratio, steps, degrees }`
- `ptzControlEnabled: true`

For the G5 PTZ specifically:
- Pan: ±175° (steps 500–35500, 0.09° per step)
- Tilt: -10° to +90° (steps 8000–18000, 0.07° per step)
- Zoom: 1× to 2× (steps 0–730)

## Live position

```
GET /proxy/protect/api/cameras/{id}/ptz/position
```

Response:
```json
{
  "degree": { "pan": -25, "tilt": 9, "zoom": 1.447 },
  "steps":  { "focus": 210, "pan": 15513, "tilt": 9910, "zoom": 234 }
}
```

`degree` is rounded; `steps` is the raw motor position.

## The move endpoint (the big one)

```
POST /proxy/protect/api/cameras/{id}/move
Content-Type: application/json
```

Body is a **discriminated union on `type`** with five variants. All variants wrap the per-type payload under a `payload` key.

### `continuous` — joystick mode

```json
{ "type": "continuous",
  "payload": { "x": -1000..1000, "y": -1000..1000, "z": -1000..1000 } }
```

- `x` = pan velocity (positive = right)
- `y` = tilt velocity (positive = down — yes, screen-space)
- `z` = zoom velocity (positive = in)
- `{x:0, y:0, z:0}` = stop any continuous movement
- Server-side dead-man timer is **20 s** — resend at ≥1 Hz to keep moving, or just send a zero on release

### `relative` — bump by an offset

```json
{ "type": "relative",
  "payload": {
    "panPos":  -4095..4095,
    "tiltPos": -4095..4095,
    "panSpeed":  0..1000,
    "tiltSpeed": 0..1000,
    "scale": "normalized"
  } }
```

Moves by the given motor-step delta from current position. `panPos:0, tiltPos:0` is a no-op.

### `zoom` — explicit zoom target

```json
{ "type": "zoom",
  "payload": {
    "zoomPos":  0..730,         // for G5 PTZ; varies by model
    "zoomSpeed": 0..1000,
    "scale": "normalized"
  } }
```

### `center` — viewport-relative click target

```json
{ "type": "center",
  "payload": { "x": 0..1000, "y": 0..1000, "z": 0..1000 } }
```

This is what the Protect web UI sends when you click a point in the live view.
- `x:500, y:500` = center (no movement)
- `x:0, y:0` = move so the upper-left becomes center
- `x:1000, y:1000` = move so the lower-right becomes center
- `z:250` = zoom out 2× from current
- `z:1000` = zoom in 2× from current
- Affected by `camera.ispSettings.isFlippedHorizontal/Vertical`

### `preset` — jump to a saved preset

```json
{ "type": "preset", "payload": { "slot": 1 } }
```

Equivalent to `POST /cameras/{id}/ptz/goto/{slot}`. Slot `-1` = home preset.

## Preset management

```
GET    /proxy/protect/api/cameras/{id}/ptz/preset       — list
POST   /proxy/protect/api/cameras/{id}/ptz/preset       — create at current position
PATCH  /proxy/protect/api/cameras/{id}/ptz/preset/{n}   — rename
DELETE /proxy/protect/api/cameras/{id}/ptz/preset/{n}   — delete
GET    /proxy/protect/api/cameras/{id}/ptz/snapshot/{n} — preset thumbnail JPEG
POST   /proxy/protect/api/cameras/{id}/ptz/goto/{n}     — jump to preset
POST   /proxy/protect/api/cameras/{id}/ptz/home         — set current as home
GET    /proxy/protect/api/cameras/{id}/ptz/home         — get home preset
```

## Patrol management

```
GET    /proxy/protect/api/cameras/{id}/ptz/patrol            — list
POST   /proxy/protect/api/cameras/{id}/ptz/patrol            — create
PATCH  /proxy/protect/api/cameras/{id}/ptz/patrol/{n}        — modify
DELETE /proxy/protect/api/cameras/{id}/ptz/patrol/{n}        — delete
GET    /proxy/protect/api/cameras/{id}/ptz/patrol/active     — currently running?
POST   /proxy/protect/api/cameras/{id}/ptz/patrol/start/{n}  — start
POST   /proxy/protect/api/cameras/{id}/ptz/patrol/stop       — stop
```

> ⚠️ Ubiquiti's docs note that the G5 PTZ motor is not rated for sustained continuous operation (24/7 patrol). Honor the duty cycle the camera was built for.

## Constants (from the Protect bundle)

```
PTZ_MIN_RELATIVE_COORD   = -4095
PTZ_MAX_RELATIVE_COORD   =  4095
PTZ_MIN_CONTINUOUS_COORD = -1000
PTZ_MAX_CONTINUOUS_COORD =  1000
PTZ_MIN_CENTER_COORD     =  0
PTZ_MAX_CENTER_COORD     =  1000
PTZ_MIN_SPEED            =  0
PTZ_MAX_SPEED            =  1000
PTZ_MIN_ZOOM             =  0
PTZ_MAX_ZOOM             =  11000
PTZ_PRESET_HOME_SLOT     =  -1
```

## Errors you'll actually see

| HTTP | Meaning |
|------|---------|
| **200** `{"success":true}` | Move accepted |
| **400** `ZOD_PARSE_ERROR` | Schema mismatch — the `issues[]` array tells you which field |
| **400** `Invalid movement parameters` | Schema OK but value out of model-specific range |
| **401** | Cookie/CSRF expired — re-auth |
| **403** | Wrong creds, or rate-limit lockout |
| **404** | Wrong camera ID, wrong route, or ancient Protect version |

## Discovery technique (how this doc was written)

If a future Protect version changes any of this, you can rediscover it:

```bash
# SSH to the NVR (root). The Protect Node.js bundle is one minified file:
cat /usr/share/unifi-protect/app/service.js | wc -l    # always 1

# Find route/path registrations
grep -oE 'path:"/cameras/:id/ptz[^"]*"' /usr/share/unifi-protect/app/service.js | sort -u

# Find the PTZ payload schema
tr '{}' '\n\n' < /usr/share/unifi-protect/app/service.js | \
  grep -E 'ptzMovePayloadSchema'

# Constants are exported with names like t.PTZ_MAX_CONTINUOUS_COORD=...
grep -oE 't\.PTZ_[A-Z_]+=[^,;]+' /usr/share/unifi-protect/app/service.js | sort -u

# OpenAPI descriptions are embedded — find the Continuous mode docs:
tr ',' '\n' < /usr/share/unifi-protect/app/service.js | \
  grep -A2 'PtzMoveType.Continuous'
```

This is how Helmsman was built. None of it required vendor docs.

---

Built by [KCCS](https://kccsonline.com).
