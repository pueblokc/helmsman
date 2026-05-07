# Changelog

## v0.2 — 2026-05-06

- **USB joystick / gamepad support** via browser GamepadAPI — works with any Xbox / PlayStation / generic HID controller, no driver install
  - Left stick = pan/tilt, triggers = zoom
  - Face buttons + D-pad = presets 1–8
  - Shoulder buttons = camera switch
  - Start = panic stop
- **Saved credentials** — NVR IP / user / password persisted to `~/.helmsman/config.json` (0600), with a "forget" link
- **Auto-connect** on launch when a saved password is present
- **Branded UI header** — inline ship's-wheel logo
- **Windows .exe build** — `build/build.ps1` produces a 14 MB single-file `Helmsman.exe`
- **Promo pack** — banner, screenshots, full user guide, reverse-engineered API reference

## v0.1 — 2026-05-06

Initial release.

- Single-file Python web app (`helmsman.py`)
- Browser-based virtual joystick UI (canvas + pointer events)
- Continuous-move PTZ control via Protect's `/cameras/{id}/move` endpoint
- Preset jump (slot 1–9), live position display, panic stop
- Speed limit / deadzone / send rate sliders
- Catppuccin Mocha theme
- Tested against Protect 7.1.46 on UNVR with G5 PTZ
