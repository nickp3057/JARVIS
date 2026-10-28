package com.tvhome;

import android.content.Context;
import android.media.AudioManager;
import android.util.Log;

/**
 * The wake-up alarm's TV half. Core's wake monitor publishes a wake_up event
 * (brain/wake_alarm.py) at the alarm time; AlertStream hands it here. The
 * packet on the event stream is what wakes the TV from network standby (TCL
 * blocks app wakeup alarms). Turns the screen on, starts the music quietly,
 * fades up to the alarm's volume over a minute and a half, and comes back to
 * the home screen so the clock and the music card are what Nick sees.
 *
 * Test from the Mac (DUMP-guarded):
 *   adb shell am broadcast -n com.tvhome/.WakeTestReceiver [--es music spotify:playlist:...] [--ei volume 30]
 */
final class Wake {
    private static final String TAG = "TvHomeWake";
    private static final long RAMP_MS = 90_000;

    private Wake() {}

    /** Blocking; call off the main thread. {@code music} empty = resume Spotify. */
    static void run(Context c, String music, int volumePercent, String label) {
        Log.i(TAG, "waking: music=" + music + " volume=" + volumePercent);
        AudioManager audio = c.getSystemService(AudioManager.class);
        int max = audio.getStreamMaxVolume(AudioManager.STREAM_MUSIC);
        int target = Math.max(1, Math.round(max * volumePercent / 100f));
        audio.setStreamVolume(AudioManager.STREAM_MUSIC, Math.max(1, target / 8), 0);
        try (SelfAdb adb = SelfAdb.connect(c, 30_000)) {
            adb.shell("input keyevent 224"); // WAKEUP: screen on, no toggle
            Thread.sleep(2500);
            if (music.isEmpty()) {
                adb.shell("monkey -p " + Music.SPOTIFY + " -c android.intent.category.LEANBACK_LAUNCHER 1");
            } else {
                adb.shell("am start -a android.intent.action.VIEW -d '" + music.replace("'", "")
                        + "' -p " + Music.SPOTIFY);
            }
            // Opening a playlist doesn't always start it; press play until it does.
            for (int i = 0; i < 6 && !Music.now(c).playing; i++) {
                Thread.sleep(3000);
                if (!Music.now(c).playing) adb.shell("input keyevent 126"); // MEDIA_PLAY
            }
            Thread.sleep(1500);
            adb.shell("input keyevent 3"); // home: clock, greeting and the music card
        } catch (Exception e) {
            Log.w(TAG, "wake-up failed", e);
        }
        Banner.show(c, Jarvis.theme(c), "reply", "Good morning, sir.",
                label.isEmpty() ? "Music's on." : label + " is on.");
        int start = audio.getStreamVolume(AudioManager.STREAM_MUSIC);
        int steps = Math.max(1, target - start);
        for (int i = 1; i <= steps; i++) {
            try {
                Thread.sleep(RAMP_MS / steps);
            } catch (InterruptedException e) {
                return;
            }
            // Stop fading if Nick has touched the volume himself.
            int now = audio.getStreamVolume(AudioManager.STREAM_MUSIC);
            if (now != start + i - 1) return;
            audio.setStreamVolume(AudioManager.STREAM_MUSIC, start + i, 0);
        }
    }
}
