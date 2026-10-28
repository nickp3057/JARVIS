package com.tvhome;

import android.content.Context;
import android.util.Log;

import java.util.regex.Pattern;

/**
 * Presses Watch/Resume on a Plex title page for "play ..." requests, with the
 * remote keys a person would press, through the TV's own adb (Plex ignores
 * accessibility clicks). Plex keeps Nick signed in, so there's no PIN screen.
 */
final class PlexWatch {
    private static final String TAG = "TvHomePlex";
    private static final Pattern FOCUSED_WATCH = Pattern.compile(
            "resource-id=\"action-button-watch\"[^>]*focused=\"true\"");

    private PlexWatch() {}

    /**
     * Waits for a title page whose Watch/Resume
     * button has focus, then presses OK. Blocking; call off the main thread.
     */
    static void pressWatch(Context context) {
        try (SelfAdb adb = SelfAdb.connect(context, 30_000)) {
            for (int i = 0; i < 20; i++) {
                Thread.sleep(1500);
                String screen = adb.shell("uiautomator dump /dev/tty 2>/dev/null");
                if (FOCUSED_WATCH.matcher(screen).find()) {
                    adb.shell("input keyevent KEYCODE_DPAD_CENTER");
                    Log.i(TAG, "pressed Watch");
                    return;
                }
            }
            Log.i(TAG, "Watch button never appeared");
        } catch (Exception e) {
            Log.w(TAG, "auto-play failed: " + e);
        }
    }
}
