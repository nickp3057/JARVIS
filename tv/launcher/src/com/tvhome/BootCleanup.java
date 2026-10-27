package com.tvhome;

import android.content.Context;
import android.util.Log;

/**
 * TCL's firmware restores some removed apps on every boot, so re-remove them
 * (and keep the Google TV launcher off) through the TV's own adb.
 */
final class BootCleanup {
    private static final String TAG = "TvHomeCleanup";

    private static final String[] REMOVE = {
            "tv.samba.ssm.ui",              // Samba TV ad tracking (ACR)
            "com.tcl.ttvs",                 // TCL Alexa / analytics
            "com.tcl.smartlink.core",       // silent app installer
            "com.tcl.tv.plus",              // TCL TV+ ad channels
            "com.tcl.partnercustomizer",    // shortcut buttons -> Play Store promos
            "com.android.printspooler",
    };
    private static final String[] DISABLE = {
            "com.google.android.apps.tv.launcherx",
            "com.google.android.tungsten.setupwraith",
    };

    private BootCleanup() {}

    /** Blocking; call off the main thread. Retries while adbd is still starting. */
    static void run(Context context) {
        StringBuilder cmd = new StringBuilder();
        for (String p : REMOVE) cmd.append("pm uninstall -k --user 0 ").append(p).append(" 2>&1; ");
        for (String p : DISABLE) cmd.append("pm disable-user --user 0 ").append(p).append(" 2>&1; ");
        for (int attempt = 0; attempt < 12; attempt++) {
            try (SelfAdb adb = SelfAdb.connect(context, 60_000)) {
                Log.i(TAG, adb.shell(cmd.toString()).trim().replace('\n', ' '));
                return;
            } catch (Exception e) {
                Log.w(TAG, "attempt " + attempt + ": " + e);
            }
            try {
                Thread.sleep(10_000);
            } catch (InterruptedException e) {
                return;
            }
        }
    }
}
