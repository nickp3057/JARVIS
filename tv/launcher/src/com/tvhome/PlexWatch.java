package com.tvhome;

import android.content.Context;
import android.content.Intent;
import android.net.Uri;
import android.util.Log;

import java.util.Locale;

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
     * Plays a Plex title from a cold start: wakes the screen and closes Plex
     * first (Plex ignores a deep link while it's already open, 2026-11-15),
     * then opens the title page and presses Watch / Resume. Blocking.
     */
    static void playFresh(Context context, String type, String title, String ratingKey) {
        try (SelfAdb adb = SelfAdb.connect(context, 30_000)) {
            adb.shell("input keyevent KEYCODE_WAKEUP");
            adb.shell("am force-stop com.plexapp.android");
            Thread.sleep(500);
        } catch (Exception e) {
            Log.w(TAG, "couldn't close Plex first: " + e);
        }
        String slug = title.toLowerCase(Locale.US).replaceAll("[^a-z0-9]+", "-").replaceAll("^-|-$", "");
        String url = "https://watch.plex.tv/libraries/" + Config.PLEX_SERVER_ID + "/" + type
                + "/" + slug + "/" + ratingKey;
        try {
            Intent i = new Intent(Intent.ACTION_VIEW, Uri.parse(url));
            i.setPackage("com.plexapp.android");
            i.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK | Intent.FLAG_ACTIVITY_CLEAR_TASK);
            context.getApplicationContext().startActivity(i);
            Log.i(TAG, "opened " + title + " for Core");
        } catch (Exception e) {
            Log.w(TAG, "couldn't open " + title + ": " + e);
            return;
        }
        pressWatch(context);
    }

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
