# Changelog

## v0.3 — 2026-05-06

Big feature drop. All endpoints smoke-tested 17/17 against a live G5 PTZ.

### New

- **Live snapshot preview** behind the helm — toggle button, 1 Hz JPEG poll. Aim while you drive.
- **Per-camera axis flips** (invert X / Y / Z) — for upside-down mounts. Persisted per camera ID in `~/.helmsman/config.json`.
- **Keyboard nudges** — arrow keys = small relative bumps, `+/-` = zoom step, `Shift` = bigger nudge, `1-9` = preset jump, `H` = home preset.
- **Inline preset save / rename / delete** — Shift+click empty slot to save current position, Shift+click filled slot to rename, Alt+click to delete. No more switching to the Protect web UI.
- **Camera control panel** — locate (flash camera LEDs), flashlight on/off (cameras that have one), IR night mode (`auto / on / off / autoFilterOnly`).
- **Move recorder + scene replay** — record stick gestures, save as named scenes (in browser localStorage), replay at original timing. Great for sweep-on-demand.
- **OS keyring password storage** — Windows Credential Manager / macOS Keychain / libsecret via the `keyring` library. Falls back to plaintext file if no keyring backend is available.
- **Telegram notifications** — set `HELMSMAN_TELEGRAM_BOT` + `HELMSMAN_TELEGRAM_CHAT` env vars for session-start + first-PTZ-command alerts (rate-limited to 1/min).

### Improved

- `Connection: close` on every response — eliminates a phantom disconnect on slower bootstrap calls.
- Backend now exposes 7 new endpoints (snapshot proxy, camera info, camera patch, save/rename/delete preset, locate, flashlight, cam_settings).
- Smoke-test suite (`.smoke-test.py`) covers every endpoint against the live NVR.

## v0.2 — 2026-05-06

- USB joystick / gamepad support via browser GamepadAPI
- Saved credentials at `~/.helmsman/config.json`
- Auto-connect on launch when password is saved
- Branded UI header with inline ship's-wheel logo
- Windows .exe build (`build/build.ps1`) — single-file ~14 MB

## v0.1 — 2026-05-06

Initial release. Single-file Python web app, virtual-joystick browser UI, continuous-move PTZ via Protect's `/cameras/{id}/move` endpoint. Tested against Protect 7.1.46 + G5 PTZ.
