package com.tvhome;

import android.content.Context;
import android.net.ConnectivityManager;
import android.net.Network;
import android.net.NetworkCapabilities;
import android.util.Log;

/**
 * Gets the TV back on Wi-Fi when the router drops it in standby. On
 * 2026-11-19 at 20:11 the access point sent DEAUTH_LEAVING with the screen
 * off; this TV can't scan for networks in standby (PNO "not supported", and
 * its scheduled scans came back empty), so it stayed off the network for 74
 * minutes, until the screen came on. A wake-up alarm in that window could
 * never have reached it.
 *
 * Every 15 s: if there's no Wi-Fi (or Ethernet) network for 30 s, ask for a
 * scan (cmd wifi start-scan, through {@link SelfAdb}, which works without a
 * network); still nothing 30 s later, turn Wi-Fi off and on again, which
 * reloads the driver and rejoins. Toggles are at least 2 minutes apart.
 * The bridge service's wake lock keeps this running in standby.
 */
final class WifiWatchdog {
    private static final String TAG = "TvHomeWifi";
    private static final long CHECK_MS = 15_000;
    private static final long SCAN_AFTER_MS = 30_000;
    private static final long TOGGLE_AFTER_MS = 60_000;
    private static final long TOGGLE_GAP_MS = 120_000;

    private WifiWatchdog() {}

    static void start(Context context) {
        Context c = context.getApplicationContext();
        Thread t = new Thread(() -> loop(c), "wifi-watchdog");
        t.setDaemon(true);
        t.start();
    }

    static boolean online(Context c) {
        ConnectivityManager cm = c.getSystemService(ConnectivityManager.class);
        for (Network n : cm.getAllNetworks()) {
            NetworkCapabilities caps = cm.getNetworkCapabilities(n);
            if (caps != null && (caps.hasTransport(NetworkCapabilities.TRANSPORT_WIFI)
                    || caps.hasTransport(NetworkCapabilities.TRANSPORT_ETHERNET))) {
                return true;
            }
        }
        return false;
    }

    private static void loop(Context c) {
        long downSince = 0, scanned = 0, toggled = 0;
        while (true) {
            try {
                Thread.sleep(CHECK_MS);
            } catch (InterruptedException e) {
                return;
            }
            long now = System.currentTimeMillis();
            if (online(c)) {
                if (downSince != 0) {
                    Log.i(TAG, "back on the network after " + (now - downSince) / 1000 + " s");
                }
                downSince = 0;
                scanned = 0;
                continue;
            }
            if (downSince == 0) {
                downSince = now;
                Log.w(TAG, "off the network");
                continue;
            }
            long down = now - downSince;
            try {
                if (down >= TOGGLE_AFTER_MS && now - toggled >= TOGGLE_GAP_MS) {
                    toggled = now;
                    Log.w(TAG, "still off after " + down / 1000 + " s; restarting Wi-Fi");
                    shell(c, "svc wifi disable; sleep 3; svc wifi enable");
                } else if (down >= SCAN_AFTER_MS && scanned == 0) {
                    scanned = now;
                    Log.w(TAG, "off for " + down / 1000 + " s; scanning");
                    shell(c, "cmd wifi start-scan");
                }
            } catch (Exception e) {
                Log.w(TAG, "couldn't reach the TV's own adb", e);
            }
        }
    }

    private static void shell(Context c, String command) throws Exception {
        try (SelfAdb adb = SelfAdb.connect(c, 15_000)) {
            adb.shell(command);
        }
    }
}
