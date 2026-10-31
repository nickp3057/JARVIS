package com.tvhome;

import android.content.Context;
import android.net.wifi.WifiManager;
import android.os.PowerManager;
import android.util.Log;

/**
 * Keeps the TV on the network in standby so JARVIS can reach it (the wake-up
 * alarm, Barn Door, the companion's remote). Without this, on 2026-10-31 the
 * TV's Wi-Fi dropped about once a minute after it was switched off at 04:54
 * (network standby on), and at 06:26 TCL's firmware took it from standby to a
 * full power-off (the next boot: "cold,powerkey"), so the 09:00 alarm had
 * nothing to wake. A partial wake lock keeps Android from ever counting the
 * TV as idle, and a low-latency Wi-Fi lock keeps the radio out of power save.
 * Both are held for as long as the bridge service runs (the whole time the
 * TV is powered). Cost: a few watts in standby instead of under one.
 */
final class StayReachable {
    private static final String TAG = "TvHomeReachable";
    private static PowerManager.WakeLock wake;
    private static WifiManager.WifiLock wifi;

    private StayReachable() {}

    static synchronized void hold(Context context) {
        Context c = context.getApplicationContext();
        try {
            if (wake == null) {
                wake = c.getSystemService(PowerManager.class)
                        .newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "tvhome:reachable");
                wake.setReferenceCounted(false);
                wake.acquire();
            }
            if (wifi == null) {
                wifi = c.getSystemService(WifiManager.class)
                        .createWifiLock(WifiManager.WIFI_MODE_FULL_LOW_LATENCY, "tvhome:reachable");
                wifi.setReferenceCounted(false);
                wifi.acquire();
            }
            Log.i(TAG, "holding wake + wifi locks");
        } catch (RuntimeException e) {
            Log.w(TAG, "couldn't take the locks", e);
        }
    }
}
