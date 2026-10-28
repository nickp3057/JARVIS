package com.tvhome;

import android.app.AlarmManager;
import android.app.PendingIntent;
import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;
import android.os.PowerManager;
import android.os.SystemClock;
import android.util.Log;

import java.util.Calendar;

/**
 * Fixes "no sound after the TV sat unused for a long time". The TV never
 * really powers off: "off" is TCL's suspend-to-RAM, and after a long stretch
 * of it the Realtek audio path can resume silent (TCL's own quirk table lists
 * NO_SOUND_AFTER_STR_RESUME). So reboot in quiescent mode while the screen
 * is off: the TV restarts in ~20 s and stays dark (verified 2026-10-27), and
 * the next power-on is a fresh boot with working audio. Two triggers:
 *  - a minute after the TV is switched off, if it has been up 20 h or more
 *    (RemoteBridgeService watches SCREEN_OFF); this is the reliable one;
 *  - a 4am alarm. TCL's AlarmManager turns every app's wakeup alarm into a
 *    non-wakeup one (ro.feature.alarm_wakeup_control), so it only runs when
 *    something else wakes the CPU in standby, possibly late.
 * Either way it never reboots while the screen is on.
 *
 * Test from the Mac (permission DUMP, so only adb can send it):
 *   adb shell am broadcast -n com.tvhome/.NightlyRefresh --el in_ms 120000 [--ez dry_run true]
 * (in_ms 0 runs it now; tests skip the uptime gate but never the screen check)
 */
public class NightlyRefresh extends BroadcastReceiver {
    private static final String TAG = "TvHomeRefresh";
    private static final int HOUR = 4;
    private static final long MIN_UPTIME_MS = 12 * 3600 * 1000L;
    static final long SCREEN_OFF_UPTIME_MS = 20 * 3600 * 1000L;

    @Override
    public void onReceive(Context context, Intent intent) {
        long inMs = intent.getLongExtra("in_ms", -1);
        if (inMs > 0) {
            schedule(context, System.currentTimeMillis() + inMs,
                    intent.getBooleanExtra("dry_run", false), true);
            return;
        }
        PendingResult result = goAsync();
        new Thread(() -> {
            try {
                run(context, intent.getBooleanExtra("dry_run", false),
                        inMs == 0 || intent.getBooleanExtra("test", false));
            } finally {
                scheduleNext(context);
                result.finish();
            }
        }, "refresh").start();
    }

    private static void run(Context c, boolean dryRun, boolean test) {
        refreshIfOff(c, "4am", test ? 0 : MIN_UPTIME_MS, dryRun);
    }

    /** Blocking. Quiescent reboot if the screen is off and uptime is at least {@code minUptimeMs}. */
    static void refreshIfOff(Context c, String why, long minUptimeMs, boolean dryRun) {
        boolean screenOn = c.getSystemService(PowerManager.class).isInteractive();
        long uptime = SystemClock.elapsedRealtime();
        String verdict;
        if (!Jarvis.flag(c, Jarvis.REFRESH) && minUptimeMs > 0) {
            verdict = why + " skipped: turned off in Settings";
        } else if (screenOn) {
            verdict = why + " skipped: screen on";
        } else if (uptime < minUptimeMs) {
            verdict = why + " skipped: up " + uptime / 60_000 + " min";
        } else if (dryRun) {
            verdict = why + " dry run: would reboot";
        } else {
            verdict = why + ": rebooting quiescent after " + uptime / 3_600_000 + " h up";
        }
        record(c, verdict);
        if (!verdict.contains("rebooting")) return;
        try (SelfAdb adb = SelfAdb.connect(c, 30_000)) {
            adb.shell("svc power reboot quiescent");
        } catch (Exception e) {
            record(c, "reboot failed: " + e);
        }
    }

    /** Arms tomorrow's (or tonight's) 4am refresh. Safe to call repeatedly. */
    static void scheduleNext(Context c) {
        Calendar next = Calendar.getInstance();
        next.set(Calendar.HOUR_OF_DAY, HOUR);
        next.set(Calendar.MINUTE, 0);
        next.set(Calendar.SECOND, 0);
        if (next.getTimeInMillis() <= System.currentTimeMillis()) next.add(Calendar.DAY_OF_MONTH, 1);
        schedule(c, next.getTimeInMillis(), false, false);
    }

    private static void schedule(Context c, long at, boolean dryRun, boolean test) {
        Intent i = new Intent(c, NightlyRefresh.class).putExtra("dry_run", dryRun).putExtra("test", test);
        PendingIntent pi = PendingIntent.getBroadcast(c, test ? 1 : 0, i,
                PendingIntent.FLAG_IMMUTABLE | PendingIntent.FLAG_UPDATE_CURRENT);
        // Inexact is fine (exact needs SCHEDULE_EXACT_ALARM); WAKEUP so it runs
        // while the TV is in standby.
        c.getSystemService(AlarmManager.class)
                .setAndAllowWhileIdle(AlarmManager.RTC_WAKEUP, at, pi);
        Log.i(TAG, "next refresh at " + new java.util.Date(at) + (test ? " (test)" : ""));
    }

    /** Keeps the last few outcomes in prefs; the logcat buffer here only holds minutes. */
    private static void record(Context c, String verdict) {
        String line = new java.util.Date() + " " + verdict;
        Log.i(TAG, line);
        String log = Jarvis.prefs(c).getString("refresh_log", "");
        String joined = line + "\n" + log;
        String[] lines = joined.split("\n");
        StringBuilder kept = new StringBuilder();
        for (int i = 0; i < Math.min(lines.length, 10); i++) kept.append(lines[i]).append('\n');
        Jarvis.prefs(c).edit().putString("refresh_log", kept.toString()).commit();
    }
}
