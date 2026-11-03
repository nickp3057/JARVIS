# Optiplex host setup

The Optiplex (`ssh jarvis`, Debian 13, ifupdown + wpa_supplicant + dhcpcd on
`wlp2s0`, no NetworkManager) runs `jarvis-api.service`. These are host-level
fixes that need `sudo`, so Nick applies them by hand.

## Wait for the network at boot

`jarvis-api.service` has `After=/Wants=network-online.target`, but
`ifupdown-wait-online.service` was disabled, so the target was reached about
2 s into boot, before Wi-Fi association and DHCP. After the 2026-09-22 power
cut every integration logged "Network is unreachable" / DNS failures in the
first second.

```bash
cd ~/jarvis && sudo sh ops/optiplex/install-network-wait.sh 10.0.0.1
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
.venv/bin/python ops/optiplex/swap_lamp.py          # finds it, changes nothing
.venv/bin/python ops/optiplex/swap_lamp.py --apply  # does the swap
sudo systemctl restart jarvis-api
```

The new lamp becomes `light.lamp` "Lamp"; the old `switch.lamp` plug is
renamed `switch.old_lamp_plug` and disabled; scenes, automations and `.env`
point at `light.lamp`. If it finds more than one new Govee light, pass the
lamp's id: `swap_lamp.py --apply light.xxx`.
