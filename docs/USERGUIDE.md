# Helmsman — User Guide

> Drag the wheel, steer the camera. A virtual joystick PTZ controller for UniFi Protect.

<p align="center">
  <img src="../assets/logo.png" alt="Helmsman" width="160"/>
</p>

---

## Table of contents

- [What you need](#what-you-need)
- [Install](#install)
- [First connect](#first-connect)
- [Driving the camera](#driving-the-camera)
- [Presets](#presets)
- [Safety controls](#safety-controls)
- [Diagnostics](#diagnostics)
- [Optional environment variables](#optional-environment-variables)
- [Troubleshooting](#troubleshooting)
- [Security model](#security-model)
- [FAQ](#faq)

---

## What you need

- **A UniFi Protect NVR** (UNVR / UDM Pro / UCK Gen2+) running Protect 7.1 or newer
- **At least one PTZ camera** adopted to it. Currently confirmed against the **G5 PTZ**; any camera the bootstrap reports with `featureFlags.isPtz: true` should work
- **A local Protect user account** (NOT a cloud SSO account, NOT the OS root)
- **Python 3.10+** with `pip` on the machine you'll run Helmsman from
- **Network reachability** to the NVR's HTTPS port (443)

You do not need:
- Anything installed on the NVR
- Anything installed on the camera
- Cloud access, an Ubiquiti account, or internet

---

## Install

```bash
git clone https://github.com/pueblokc/helmsman.git
cd helmsman
pip install -r requirements.txt
python helmsman.py
```

On Windows you can double-click `run.bat` instead of typing the last command.

The console prints:

```
Helmsman — UniFi Protect virtual joystick PTZ controller
  Listening on http://127.0.0.1:8765
  Open in your browser, fill in NVR IP / user / password, click Connect.
```

A browser tab opens to `http://127.0.0.1:8765`. If it doesn't, paste that URL in.

---

## First connect

![Disconnected state](../assets/screenshot-disconnected.png)

Fill in:

- **NVR IP / hostname** — `192.168.x.x` or `nvr.lan`. No `https://`, no port.
- **Username** — your Protect *local* user (e.g. `localadmin`). Cloud SSO accounts won't work.
- **Password** — that user's password.

Click **Connect & Bootstrap**.

If creds are right, the badge flips to **connected** and the camera dropdown fills with PTZ-capable cameras.

![Connected, idle](../assets/screenshot-connected.png)

---

## Driving the camera

Pick a camera from the dropdown. The position display under it shows the live pan/tilt/zoom (refreshed every 2 s).

**Pan and tilt** — click and drag the round helm. Release to stop.

**Zoom** — click and drag the vertical strip on the right. Release to stop.

**Speed** is proportional to how far you push the stick. A small deflection = slow nudge; full deflection = top speed.

![Active session](../assets/screenshot-active.png)

The whole UI keeps you informed:
- Bars under the helm show real-time `x / y / z` values being sent (range -1000..1000)
- The **LAST MOVE COMMAND** panel shows the exact JSON payload posted to the NVR
- The **LOG** panel logs every API call with status code

### Stopping

Three ways:

1. **Release the stick** — sends a single zero-vector immediately
2. **Press `Esc`** — fires PANIC STOP
3. **Click the red PANIC STOP button** — same as Esc

Even if everything else fails (browser crash, network drop, you trip over the cable), the NVR's own dead-man timer auto-stops the camera after ~20 s of no commands.

---

## Presets

Helmsman reads any presets you've already saved in the Protect web UI and shows them as buttons in the lower-left. Click one to jump.

- Slot numbers `1-9` are user-defined presets in Protect.
- Greyed-out buttons mean no preset exists for that slot.
- The "Refresh presets" button in the Diagnostics panel re-pulls the list (useful if you just created one in the Protect UI).

> Note: Helmsman does not (yet) create or delete presets from the UI. Use the Protect app for that. We may add inline preset save in a future version.

---

## Safety controls

Three sliders in the **SAFETY** panel:

| Slider | Default | What it does |
|--------|---------|-------------|
| **Speed limit** | 100% | Multiplies the output. Pin it at 50% while you're learning the feel. |
| **Deadzone** | 8% | How far the stick must move from center before any command goes out. Prevents tiny jitter. |
| **Send rate** | 10 Hz | How often the bridge re-posts the move command while the stick is held. Lower = less network chatter; higher = smoother control. |

You can also stop *all* movement by setting **Speed limit** to 0% mid-drag — the bridge will start sending zero-vectors every frame.

---

## Diagnostics

| Button | What it does |
|--------|--------------|
| **Test /move route shape** | Sends a single `{x:0, y:0, z:0}` to confirm the route + payload are valid. The camera does not move. Use this when you're not sure if creds / network / camera are healthy. |
| **GET position now** | Polls position immediately instead of waiting for the 2 s refresh. |
| **Refresh presets** | Re-pulls the saved-preset list from the camera. |

The **LOG** panel on the right shows everything: connection attempts, every preset command, response codes. The **LAST MOVE COMMAND** panel shows the most recent move payload, which is invaluable when adapting Helmsman to talk to other PTZ APIs.

---

## Optional environment variables

For unattended startup, lab setups, or kiosks, you can pre-fill the form via env vars:

```bash
export HELMSMAN_NVR_IP=192.168.1.50
export HELMSMAN_USER=localadmin
export HELMSMAN_PASS=your-password
export HELMSMAN_PORT=8765        # default 8765
export HELMSMAN_BIND=127.0.0.1   # use 0.0.0.0 to listen on the LAN
python helmsman.py
```

Anything not set → blank UI field. Anything set → pre-filled but still editable.

> **`HELMSMAN_BIND=0.0.0.0`** exposes the joystick UI on your LAN. Anyone on the network can drive the camera — there's no auth on the local UI. Use only on trusted networks (or behind a reverse proxy with auth).

---

## Troubleshooting

### `Invalid username or password`
You used a cloud SSO account or the OS root password. Helmsman needs a **local Protect user**. Create one in Protect → Users → Add User → set "Local Access Only".

### `SSO Account locked`
You hit Protect's bad-login lockout (~5 attempts). It auto-clears in 10–15 minutes. Don't loop on bad creds — the lockout will just keep extending.

### `404 not found` from `/move`
You're on a Protect version older than 7.1, or Ubiquiti renamed the route. Open `/usr/share/unifi-protect/app/service.js` on the NVR and grep for `ptzMovePayloadSchema` — the line above it will reveal the current route.

### `400 Failed to parse 'request-body'`
You're hitting the move route, but the payload shape changed. The Helmsman UI builds:
```json
{"type":"continuous","payload":{"x":-1000..1000,"y":-1000..1000,"z":-1000..1000}}
```
If the NVR rejects this, check the error response — it usually tells you exactly which field is missing or out of range.

### Camera moves but in the wrong direction
- **Stick up = camera looks down (or vice versa)** → some PTZ controllers use the inverse convention. Edit `helmsman.py`, find the `sy =` line in the `tick()` function, flip the sign.
- **Camera mounted upside down** → check `featureFlags.isFlippedVertical` and `isFlippedHorizontal` in the bootstrap response. Helmsman does not currently auto-correct for these — flip the relevant axis manually.

### Helm doesn't respond to my real joystick / gamepad
This release uses an on-screen virtual joystick. Hardware joystick / gamepad / Stream Deck input is on the roadmap but not in v0.1.

### Browser tab opens but page is blank
Most likely you have an old cached version after an update. Hard refresh with `Ctrl-Shift-R` (Win/Linux) or `Cmd-Shift-R` (macOS).

---

## Security model

- Helmsman runs **locally**. All UI traffic stays on your machine.
- The bridge **only** opens a connection from your machine to the NVR you specify.
- Credentials are kept **in memory** in the Python process. Nothing is written to disk.
- The local web UI has **no authentication** — anyone with access to `127.0.0.1:8765` can drive the camera. By default it binds to localhost only, which means only your machine can reach it.
- Setting `HELMSMAN_BIND=0.0.0.0` exposes it on your LAN. Don't do this on untrusted networks, or put a reverse proxy with auth in front.
- The NVR session cookie is short-lived. If you leave the bridge idle long enough, you may need to reconnect.

---

## FAQ

**Is this an Ubiquiti product?**
No. Independent project, no affiliation with Ubiquiti.

**Will this brick my camera?**
Vanishingly unlikely. Helmsman uses the same internal API routes that Protect's own web UI uses for click-to-move PTZ. The NVR has motor-fault detection, position limits, and a server-side dead-man timer.

**Will it wear out my motor?**
The G5 PTZ datasheets warn that the motor isn't rated for sustained continuous operation (auto-patrol, 24/7 sweep). Human-driven joystick use is fine — the motor only runs while you're actively pushing the stick.

**Does it work with G4 PTZ?**
Should — the API surface is shared across PTZ models. We've confirmed it against G5 PTZ. Reports from G4 owners welcome.

**Can it run in a tab on my phone?**
Yes. The virtual stick handles touch + pointer events. Hit the URL from a phone on the same network (you'll need `HELMSMAN_BIND=0.0.0.0` or a reverse proxy).

**Where do the screenshots live in the repo?**
`assets/` (PNGs + SVGs). The screenshot driver itself (`assets/.screenshot.py`) is excluded from packaging.

---

Built by [KCCS](https://kccsonline.com).
