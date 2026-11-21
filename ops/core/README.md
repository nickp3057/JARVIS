# Core host setup

Core runs on the Surface Laptop 3 since 2026-11-10 (`ssh jarvis`, LAN 10.0.0.73,
Tailscale 100.69.8.11); deploy with `deploy/surface.sh`. The Optiplex is retired;
the notes below were written for it (Debian 13, ifupdown + wpa_supplicant +
dhcpcd on `wlp2s0`), so check them against the Surface before reusing them. It ran `jarvis-api.service`. These are host-level
fixes that need `sudo`, so Nick applies them by hand.

## Wait for the network at boot

`jarvis-api.service` has `After=/Wants=network-online.target`, but
`ifupdown-wait-online.service` was disabled, so the target was reached about
2 s into boot, before Wi-Fi association and DHCP. After the 2026-09-22 power
cut every integration logged "Network is unreachable" / DNS failures in the
first second.

```bash
cd ~/jarvis && sudo sh ops/core/install-network-wait.sh 10.0.0.1
```

This sets `WAIT_ONLINE_METHOD=ping`, `WAIT_ONLINE_ADDRESS=<gateway>` and
`WAIT_ONLINE_TIMEOUT=90` in `/etc/default/networking` (backup kept) and
enables the wait unit. Verify after a reboot with
`systemd-analyze critical-chain jarvis-api.service`: `network-online.target`
should be behind `ifupdown-wait-online.service`, a few seconds in.

The app also tolerates a slow link on its own: for `STARTUP_NETWORK_GRACE_S`
(120 s) after start, monitors retry without recording outages, weather retries
with backoff instead of waiting 30 minutes, and the calendar retries a token
refresh that failed for network reasons.

## Power back on after an outage

After a power cut the Optiplex stays off until someone presses the button. Set
the BIOS to power on when AC returns:

1. Reboot, press **F2** at the Dell logo for BIOS Setup.
2. **Power Management → AC Recovery** → choose **Power On** (not "Power Off"
   or "Last Power State"; "Last Power State" also works if it was on).
3. While there, set **Deep Sleep Control** to **Disabled** so wake/AC events
   are honoured. Apply, exit.

Alternatively, with Dell Command | Configure installed:
`sudo cctk --AcPwrRcvry=On`. Worth pairing with a small UPS for the Optiplex,
NAS and router so short blips never reach them.

## Google Calendar consent (headless)

See `JARVIS.md` → "Google Calendar setup": `ssh -L 8765:localhost:8765 jarvis`,
then `.venv/bin/python -m integrations.google_calendar --auth`.

## Swap the lamp plug for the Govee lamp (2026-11-03)

Add the lamp in the Govee app first (and turn on LAN Control in its device
settings), then:

```bash
cd ~/jarvis && git pull
.venv/bin/python ops/core/swap_lamp.py          # finds it, changes nothing
.venv/bin/python ops/core/swap_lamp.py --apply  # does the swap
sudo systemctl restart jarvis-api
```

The new lamp becomes `light.lamp` "Lamp"; the old `switch.lamp` plug is
renamed `switch.old_lamp_plug` and disabled; scenes, automations and `.env`
point at `light.lamp`. If it finds more than one new Govee light, pass the
lamp's id: `swap_lamp.py --apply light.xxx`.

## Room rainbows: lamp and fan ring (2026-11-04)

First, in Smart Life: Scenes → + → Launch Tap-to-Run → action: the fan
light, Scene mode, its rainbow; name it "Fan Light Rainbow". Then:

```bash
cd ~/jarvis && git pull
.venv/bin/python ops/core/room_rainbows.py   # or: ... room_rainbows.py scene.<tuya_scene>
sudo systemctl restart jarvis-api
```

Scenes that turn the desk lights on also put the lamp in its Rainbow; Movie
no longer turns the fan light off; the Tap-to-Run becomes
`scene.fan_light_rainbow` (the apps' Rainbow button for the fan light); and
the automation "Fan light follows the strip" turns the ring's rainbow on and
off with the strip lights. Without the Tap-to-Run it does the scenes and
tells you what's missing; run it again after.
