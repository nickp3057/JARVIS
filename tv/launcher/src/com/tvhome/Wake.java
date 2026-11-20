package com.tvhome;

import android.content.Context;
import android.media.AudioManager;
import android.net.Uri;
import android.os.Handler;
import android.os.Looper;
import android.util.Log;

/**
 * The wake-up alarm's TV half. Core's wake monitor publishes a wake_up event
 * (brain/wake_alarm.py) at the alarm time; AlertStream hands it here. The
 * packet on the event stream is what wakes the TV from network standby (TCL
 * blocks app wakeup alarms). Turns the screen on, starts the music at the
 * alarm's volume (Nick didn't like a fade that kept getting louder) and puts
 * up WakeActivity: I'm up (the music stops), or Snooze (pause, then the same
 * again in 9 min).
 *
 * Test from the Mac (DUMP-guarded):
 *   adb shell am broadcast -n com.tvhome/.WakeTestReceiver [--es music spotify:playlist:...] [--ei volume 30]
 */
final class Wake {
    private static final String TAG = "TvHomeWake";
    static final long SNOOZE_MS = 9 * 60_000;
    private static final Handler MAIN = new Handler(Looper.getMainLooper());
    private static Runnable snoozed;
    /** When the snooze ends (wall-clock ms), 0 if not snoozing; WakeActivity shows it. */
    static volatile long snoozeUntil;
    /** When the last alarm went off (wall-clock ms), so I'm up only stops wake-up music. */
    private static volatile long rangAt;
    /** When I'm up was last pressed (wall-clock ms): a wake still starting stops. */
    private static volatile long dismissedAt;

    private Wake() {}

    /** Blocking; call off the main thread. {@code music}: spotify: URI, "search:words", or "" to resume. */
    static void run(Context c, String music, int volumePercent, String label) {
        Log.i(TAG, "waking: music=" + music + " volume=" + volumePercent);
        long started = System.currentTimeMillis();
        rangAt = started;
        cancelSnooze(); // a snooze ending (ours or Core's) lands here
        AudioManager audio = c.getSystemService(AudioManager.class);
        int max = audio.getStreamMaxVolume(AudioManager.STREAM_MUSIC);
        int target = Math.max(1, Math.round(max * volumePercent / 100f));
        audio.setStreamVolume(AudioManager.STREAM_MUSIC, target, 0);
        try (SelfAdb adb = SelfAdb.connect(c, 30_000)) {
            adb.shell("input keyevent 224"); // WAKEUP: screen on, no toggle
            adb.shell("input keyevent 3"); // home: clock, greeting and the music card
            Thread.sleep(2500);
            // A reconnect replays the wake-up and I'm up together; don't start
            // music that was already turned down.
            if (dismissedAt >= started) {
                Log.i(TAG, "already answered; not starting the music");
                return;
            }
            // Through Spotify's media session, so Spotify never takes the screen.
            if (!Music.playInBackground(c, music)) {
                // Spotify hasn't run since boot: open it once, then play through it.
                adb.shell("monkey -p " + Music.SPOTIFY + " -c android.intent.category.LEANBACK_LAUNCHER 1");
                for (int i = 0; i < 10 && Music.spotify(c) == null; i++) Thread.sleep(1000);
                if (!Music.playInBackground(c, music) && music.startsWith("spotify:")) {
                    adb.shell("am start -a android.intent.action.VIEW -d '" + music.replace("'", "")
                            + "' -p " + Music.SPOTIFY);
                }
            }
            // Opening a playlist doesn't always start it; press play until it does.
            for (int i = 0; i < 6 && !Music.now(c).playing; i++) {
                Thread.sleep(3000);
                if (!Music.now(c).playing) adb.shell("input keyevent 126"); // MEDIA_PLAY
            }
            // A session left over from last night can ignore playFromUri and sit
            // paused on the old track (2026-10-28): open the link in Spotify itself.
            if (!Music.now(c).playing && !music.isEmpty()) {
                String uri = music.startsWith("search:") ? "spotify:search:" + Uri.encode(music.substring(7)) : music;
                Log.w(TAG, "still not playing; opening " + uri);
                adb.shell("am start -a android.intent.action.VIEW -d '" + uri.replace("'", "")
                        + "' -p " + Music.SPOTIFY);
                for (int i = 0; i < 4 && !Music.now(c).playing; i++) {
                    Thread.sleep(3000);
                    if (!Music.now(c).playing) adb.shell("input keyevent 126");
                }
            }
            Thread.sleep(1500);
            if (dismissedAt >= started) {
                Music.pause(c);
                return;
            }
            // I'm up / Snooze over the home screen (also covers Spotify if it had to open).
            adb.shell("am start -n " + c.getPackageName() + "/.WakeActivity --ei volume " + volumePercent
                    + " --es label '" + label.replace("'", "") + "'");
        } catch (Exception e) {
            Log.w(TAG, "wake-up failed", e);
            Banner.show(c, Jarvis.theme(c), "reply", "Good morning, sir.",
                    label.isEmpty() ? "Music's on." : label + " is on.");
        }
    }

    /** Pause now and wake again in 9 minutes, picking up where the music stopped. */
    static void snooze(Context c, int volumePercent, String label) {
        cancelSnooze();
        Music.pause(c);
        snoozeUntil = System.currentTimeMillis() + SNOOZE_MS;
        Context app = c.getApplicationContext();
        snoozed = () -> {
            snoozeUntil = 0;
            new Thread(() -> run(app, "", volumePercent, label), "wake-snooze").start();
        };
        MAIN.postDelayed(snoozed, SNOOZE_MS);
        Log.i(TAG, "snoozed for " + SNOOZE_MS / 60_000 + " min");
    }

    /** Snoozed on the phone: pause and count down; Core rings again itself. */
    static void snoozedByCore(Context c, long untilMs) {
        cancelSnooze();
        Music.pause(c);
        snoozeUntil = untilMs > 0 ? untilMs : System.currentTimeMillis() + SNOOZE_MS;
    }

    /** I'm up (here or on the phone): stop the music and close the wake screen. */
    static void dismiss(Context c) {
        dismissedAt = System.currentTimeMillis();
        boolean alarm = snoozeUntil > 0 || System.currentTimeMillis() - rangAt < 60 * 60_000;
        cancelSnooze();
        if (alarm) {
            Music.pause(c);
            rangAt = 0;
        }
        MAIN.post(WakeActivity::closeShowing);
    }

    static void cancelSnooze() {
        if (snoozed != null) MAIN.removeCallbacks(snoozed);
        snoozed = null;
        snoozeUntil = 0;
    }
}
