package com.tvhome;

import android.content.Context;
import android.os.SystemClock;
import android.util.Log;

import java.util.regex.Pattern;

/**
 * Drives Plex with the remote keys a person would press, through the TV's own
 * adb (Plex ignores accessibility clicks): gets past the "Who's watching?" PIN
 * screen, and presses Watch/Resume on a title page for "play ..." requests.
 */
final class PlexAutoLogin {
    private static final String TAG = "TvHomeLogin";
    private static final long COOLDOWN_MS = 20_000;
    private static final Pattern FOCUSED_WATCH = Pattern.compile(
            "resource-id=\"action-button-watch\"[^>]*focused=\"true\"");
    private static long lastRun;

    private PlexAutoLogin() {}

    /** Blocking; call off the main thread when Plex comes to the foreground. */
    static synchronized void run(Context context) {
        long now = SystemClock.elapsedRealtime();
        if (now - lastRun < COOLDOWN_MS) return;
        lastRun = now;
        try (SelfAdb adb = SelfAdb.connect(context, 30_000)) {
            for (int i = 0; i < 12; i++) {
                String screen = adb.shell("uiautomator dump /dev/tty 2>/dev/null");
                if (screen.contains("watching?")) {
                    boolean pinPad = screen.contains("text=\"0\"") && screen.contains("text=\"9\"");
                    if (!pinPad) adb.shell("input keyevent KEYCODE_DPAD_CENTER; sleep 1.5");
                    StringBuilder keys = new StringBuilder("input keyevent");
                    for (char c : Config.PLEX_PIN.toCharArray()) keys.append(" KEYCODE_").append(c);
                    adb.shell(keys.toString());
                    Log.i(TAG, "signed in to Plex profile");
                    return;
                }
                Thread.sleep(1500);
            }
        } catch (Exception e) {
            Log.w(TAG, "auto-login failed: " + e);
        }
    }

    /**
     * Waits (through a possible PIN screen) for a title page whose Watch/Resume
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
