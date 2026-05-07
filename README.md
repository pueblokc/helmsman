<p align="center">
  <img src="assets/logo-banner.png" alt="Helmsman — virtual joystick PTZ controller for UniFi Protect"/>
</p>

<p align="center">
  <em>Drag the wheel — your camera moves. A virtual joystick PTZ controller for UniFi Protect.</em>
</p>

<p align="center">
  <a href="docs/USERGUIDE.md"><img src="https://img.shields.io/badge/docs-user_guide-89b4fa?style=flat-square" alt="User guide"/></a>
  <a href="docs/API-REFERENCE.md"><img src="https://img.shields.io/badge/docs-API_reference-cba6f7?style=flat-square" alt="API reference"/></a>
  <img src="https://img.shields.io/badge/python-3.10+-a6e3a1?style=flat-square" alt="Python 3.10+"/>
  <img src="https://img.shields.io/badge/protect-7.1+-f9e2af?style=flat-square" alt="Protect 7.1+"/>
  <img src="https://img.shields.io/badge/license-proprietary-f38ba8?style=flat-square" alt="Proprietary"/>
</p>

---

## What is this

Ubiquiti's UniFi Protect web app lets you click a point in a PTZ camera's live view to swing toward it, but it doesn't ship a joystick mode. **Helmsman is that joystick mode.**

Spin up a single Python file, open the browser tab it pops, and drive any PTZ-capable Protect camera from a virtual stick that talks to the same internal API the Protect web UI uses for click-and-drag.

<p align="center">
  <img src="assets/screenshot-active.png" alt="Helmsman driving a G5 PTZ" width="900"/>
</p>

---

## Features

- 🕹️ **Virtual joystick** with smooth velocity control (mouse + touch + pointer events)
- 🔍 **Separate zoom strip** for one-handed zooming while you pan/tilt
- ⚡ **Continuous-move at 5–20 Hz** — proportional speed, snaps to stop on release
- 🎯 **Preset jump** — auto-pulls saved presets, click to swing
- 🛡️ **Server-side dead-man timer** — NVR auto-stops the camera if the bridge dies (~20 s)
- ⌨️ **Panic stop** on `Esc` and a big red button
- 📏 **Speed limit / deadzone / send rate** sliders for tuning feel and safety
- 📊 **Live position + payload log** — see exactly what's being sent
- 🎨 **Catppuccin Mocha** theme, KCCS branded
- 📦 **Single Python file**, one dependency (`requests`), runs anywhere

---

## Run it

```bash
git clone https://github.com/pueblokc/helmsman.git
cd helmsman
pip install -r requirements.txt
python helmsman.py
```

Browser opens to `http://127.0.0.1:8765`. Type your NVR IP, your local Protect username and password, click **Connect & Bootstrap**.

Windows: double-click `run.bat`.

> 📖 **Read the [User Guide](docs/USERGUIDE.md)** for first-connect walkthrough, troubleshooting, security model, and FAQ.

---

## Screenshots

<table>
<tr>
<td width="50%">
<p align="center"><b>Disconnected — fresh open</b></p>
<img src="assets/screenshot-disconnected.png" alt="Disconnected state"/>
</td>
<td width="50%">
<p align="center"><b>Connected — pick a PTZ camera, see its presets</b></p>
<img src="assets/screenshot-connected.png" alt="Connected state"/>
</td>
</tr>
<tr>
<td colspan="2">
<p align="center"><b>Active — stick deflected, camera swinging, payload visible in real time</b></p>
<img src="assets/screenshot-active.png" alt="Active session"/>
</td>
</tr>
</table>

---

## How it works

```
[browser stick]  --POST /api/move-->  [helmsman.py]  --HTTPS POST--> [NVR /proxy/protect/api/cameras/:id/move]
                                       (cookie + CSRF)                  {type:"continuous", payload:{x,y,z}}
```

`helmsman.py` is a tiny HTTP server that:
1. Serves the joystick UI (HTML + JS, embedded inline)
2. Authenticates against your NVR (HTTPS, cookie + CSRF)
3. Forwards joystick deflection to Protect's continuous-move endpoint at your chosen rate

The continuous-move endpoint is the **same internal API** the Protect web UI uses for click-to-move. We're inside the supported envelope, just exposing it as a stick.

> 🧪 **Want the full reverse-engineered API map?** See the [API Reference](docs/API-REFERENCE.md).

---

## Verified

Tested against:
- **NVR:** UNVR (Debian 11 / aarch64), Protect **7.1.46**
- **Camera:** UVC G5 PTZ (`featureFlags.isPtz: true`, pan ±175°, tilt -10°/+90°, zoom 1×–2×)

Should also work with:
- G4 PTZ (same API surface, untested by us — reports welcome)
- Any future Protect camera that exposes `featureFlags.isPtz: true` in the bootstrap

---

## Roadmap

- [ ] Hardware joystick / gamepad support (XInput / DirectInput / HID)
- [ ] Stream Deck integration for preset buttons
- [ ] Inline preset save (currently view-only)
- [ ] Per-camera axis-flip toggle (for upside-down mounts)
- [ ] Multi-camera quick-switch
- [ ] Optional auth on the local UI
- [ ] Native installer / single-binary build

---

## Contributing

This is a KCCS internal tool released as a courtesy. Issues and PRs welcome but not actively solicited. If you build something cool with it, let us know — we'd love to hear.

## License

Proprietary — © 2026 KCCS. All rights reserved.

---

<p align="center">
  Built with 🔭 by <a href="https://kccsonline.com"><b>KCCS</b></a>
</p>
