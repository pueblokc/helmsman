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

### Option A — Windows one-click

Grab `Helmsman.exe` from the [Releases page](https://github.com/pueblokc/helmsman/releases) and double-click. No Python install required.

### Option B — From source (any OS)

```bash
git clone https://github.com/pueblokc/helmsman.git
cd helmsman
pip install -r requirements.txt
python helmsman.py
```

On Windows from source you can double-click `run.bat` instead of typing the last command.

### Option C — Build your own .exe

```powershell
pip install pyinstaller pillow
powershell -ExecutionPolicy Bypass -File build/build.ps1
# -> dist/Helmsman.exe (~14 MB)
```

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

### USB joystick / gamepad

Plug in any standard HID gamepad (Xbox, PlayStation, generic, flight stick, etc.) **before or while Helmsman is open**. Press any button on it once — browsers won't expose a gamepad until you've interacted with it. A `🎮` badge then appears next to the NVR status.

Default mapping (Xbox-style controllers):

| Input | Action |
|-------|--------|
| Left stick X / Y | pan / tilt (overrides on-screen helm when deflected) |
| Right trigger (RT) | zoom in |
| Left trigger (LT) | zoom out |
| A / B / X / Y | preset 1 / 2 / 3 / 4 |
| D-pad ↑ / ↓ / ← / → | preset 5 / 6 / 7 / 8 |
| LB / RB | previous / next camera |
| Start | panic stop |

Gamepad input takes over whenever any axis exceeds 5% of full deflection — release the stick and the on-screen helm regains control. The same speed-limit / deadzone / send-rate sliders apply.

> Most browsers (Chrome, Edge, Firefox) support GamepadAPI natively. Safari requires HTTPS in some configurations.

### Stopping

Three ways:

1. **Release the stick** — sends a single zero-vector immediately
2. **Press `Esc`** — fires PANIC STOP
3. **Click the red PANIC STOP button** — same as Esc

Even if everything else fails (browser crash, network drop, you trip over the cable), the NVR's own dead-man timer auto-stops the camera after ~20 s of no commands.

---

## Live snapshot preview

Click the **Live preview** button under the camera select. A 1 Hz JPEG poll starts and the image appears behind the helm at 85% opacity, so you can see what you're aiming at while you drive. Click again to hide.

For higher-frequency feedback, drop the `Send rate` slider to 5 Hz so move commands don't compete for bandwidth with the snapshot.

## Camera controls

Three buttons + a dropdown:

| Control | What it does |
|---------|--------------|
| **Locate (LED)** | Flashes the camera's status LEDs — handy for "which one is this in the rack/mount". |
| **Flashlight** | Toggles the integrated white-light flashlight on cameras that have one. Greyed out otherwise. |
| **IR night mode** | `auto`, `on`, `off`, `auto (filter only)`. Saved on the camera. |

Below those: **invert X / Y / Z** checkboxes. Per-camera setting — useful when a camera is mounted upside-down or sideways and you want stick-up to mean "up in the world." Saved per-camera in `~/.helmsman/config.json`.

## Keyboard shortcuts

| Key | Action |
|-----|--------|
| `Esc` | Panic stop |
| `← → ↑ ↓` | Small relative bump (~200 steps) |
| `Shift` + arrow | Bigger bump (~600 steps) |
| `+ / -` | Zoom in / out one step |
| `1`–`9` | Jump to preset slot |
| `H` | Jump to home preset |

These are **relative** moves (single bump, not continuous), so the camera stops automatically after each press.

## Presets

Helmsman reads any presets you've already saved in the Protect web UI and shows them as buttons in the lower-left. Click one to jump.

- Slot numbers `1-9` are user-defined presets in Protect.
- Greyed-out buttons mean no preset exists for that slot.
- The "Refresh presets" button in the Diagnostics panel re-pulls the list (useful if you just created one in the Protect UI).

- **Save current position** — `Shift`+click any empty preset slot. Helmsman prompts for a name, posts to the NVR, and refreshes the list.
- **Rename** — `Shift`+click a filled slot.
- **Delete** — `Alt`+click a filled slot. (Confirmation dialog first.)
- **Refresh presets** — in the Diagnostics panel; re-pulls the list from the NVR.

## Move recorder

Capture a sequence of stick movements and replay it on demand. Useful for "sweep the property" routines without using the patrol API (which is rate-limited by the motor).

1. Select a camera and click **● REC**. The button turns red and a `REC` badge appears.
2. Drive the camera however you like — the bridge timestamps every move command sent.
3. Click **■ Stop** to end recording.
4. Click **Save** to name and store the scene (saved in browser localStorage, not on the NVR).
5. Pick the scene from the dropdown and hit **▶ Play** to replay at the original timing.

> Stored client-side only. Clearing the browser's storage erases scenes. To export, copy the `helmsman.scenes` key from `localStorage` (DevTools → Application → Local Storage).

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

## Saved credentials

Below the password field there are two checkboxes:

- **remember** — saves NVR IP + username to `~/.helmsman/config.json` after a successful connect, so the next launch pre-fills them. (No password yet.)
- **save password** — also stores the password in the same file. Helmsman then **auto-connects** on next launch — you skip the form entirely.

The config file is created with `0600` permissions on POSIX. On Windows it lives under `%USERPROFILE%\.helmsman\config.json`, inside your user profile.

A red **forget** link appears next to the checkboxes once anything is saved — click to wipe the file and clear the form.

> **Storage backend (v0.3+):**
> - **OS keyring** is used when available — Windows Credential Manager on Windows, Keychain on macOS, libsecret/kwallet on Linux.
> - **Plaintext fallback:** if the `keyring` library isn't installed (or is unavailable on a headless Linux without `libsecret`), Helmsman saves the password to the same `config.json` instead.
> - **Either way**, anyone with read access to your user profile / Credential Manager can read it. The threat model is "convenience for trusted desktop user," not "secure password vault."

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

### My USB joystick / gamepad isn't being detected
Helmsman uses the browser's GamepadAPI. Plug in your controller, then **press any button on it** — most browsers don't expose a gamepad until you've interacted with it. Once detected, a 🎮 badge appears next to the connection status.

If your stick isn't recognized at all, check it works at https://hardwaretester.com/gamepad first.

### Stream Deck / macro keyboard
Stream Deck integration is on the roadmap. For now you can use Stream Deck's "Open URL" or "System: Hotkey" actions to send `Esc` (panic stop) or open the Helmsman URL.

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
