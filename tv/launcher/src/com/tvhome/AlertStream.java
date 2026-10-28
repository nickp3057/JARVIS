package com.tvhome;

import android.content.Context;
import android.os.PowerManager;
import android.util.Log;

import org.json.JSONObject;

import java.io.BufferedReader;
import java.io.InputStreamReader;
import java.net.HttpURLConnection;
import java.nio.charset.StandardCharsets;

/**
 * Follows Core's live alert stream (GET /api/clients/events, server-sent
 * events): every alert refreshes the home screen, and fresh warnings and
 * criticals show as a banner over whatever is on screen, Plex included.
 * Each connection replays Core's recent history (ids restart at 1 whenever
 * Core restarts, so a remembered id can't be trusted); only alerts under two
 * minutes old count, and each one only once.
 */
final class AlertStream {
    private static final String TAG = "TvHomeAlerts";
    private static final long FRESH_MS = 2 * 60 * 1000;
    /** "id@created_at" of fresh alerts already handled, across reconnects. */
    private static final java.util.Set<String> SEEN = new java.util.HashSet<>();

    private AlertStream() {}

    static void start(Context context) {
        Thread t = new Thread(() -> loop(context.getApplicationContext()), "alerts");
        t.setDaemon(true);
        t.start();
    }

    private static void loop(Context c) {
        long backoff = 5_000;
        while (true) {
            if (Jarvis.provisioned(c)) {
                try {
                    follow(c);
                    backoff = 5_000;
                } catch (Exception e) {
                    Log.i(TAG, "stream dropped: " + e);
                    // Also rediscovers Core if its address moved.
                    try {
                        Jarvis.refresh(c);
                    } catch (Exception ignored) {
                    }
                }
            }
            try {
                Thread.sleep(backoff);
            } catch (InterruptedException e) {
                return;
            }
            backoff = Math.min(backoff * 2, 60_000);
        }
    }

    private static void follow(Context c) throws Exception {
        // Core keeps a keep-alive every 15 s, so 45 s of silence means it's gone.
        HttpURLConnection conn = Jarvis.open(Jarvis.base(c), "/api/clients/events?since=0",
                Jarvis.token(c), 45_000);
        conn.setRequestProperty("Accept", "text/event-stream");
        try {
            int code = conn.getResponseCode();
            if (code != 200) throw new java.io.IOException("HTTP " + code);
            Log.i(TAG, "following " + Jarvis.base(c));
            BufferedReader in = new BufferedReader(
                    new InputStreamReader(conn.getInputStream(), StandardCharsets.UTF_8));
            StringBuilder data = new StringBuilder();
            String line;
            while ((line = in.readLine()) != null) {
                if (line.startsWith("data:")) {
                    data.append(line.substring(5).trim());
                } else if (line.isEmpty() && data.length() > 0) {
                    handle(c, new JSONObject(data.toString()));
                    data.setLength(0);
                }
            }
        } finally {
            conn.disconnect();
        }
    }

    private static void handle(Context c, JSONObject alert) {
        long id = alert.optLong("id");
        long age = System.currentTimeMillis() - (long) (alert.optDouble("created_at") * 1000);
        boolean wake = "wake_up".equals(alert.optString("kind"));
        // A wake-up can arrive late if the TV took a while to come out of standby.
        if (age >= (wake ? 10 * FRESH_MS / 2 : FRESH_MS)) return;
        synchronized (SEEN) {
            if (!SEEN.add(id + "@" + alert.optDouble("created_at"))) return;
            if (SEEN.size() > 500) SEEN.clear();
        }
        if (wake) {
            JSONObject ctx = alert.optJSONObject("context");
            if (ctx == null) ctx = new JSONObject();
            String music = ctx.optString("music"), label = ctx.optString("label");
            int volume = ctx.optInt("volume", 30);
            new Thread(() -> Wake.run(c, music, volume, label), "wake").start();
            return;
        }
        new Thread(() -> {
            try {
                Jarvis.refresh(c);
            } catch (Exception ignored) {
            }
        }).start();
        String severity = alert.optString("severity", "warning");
        boolean screenOn = c.getSystemService(PowerManager.class).isInteractive();
        Log.i(TAG, "alert " + id + " [" + severity + "] " + alert.optString("title")
                + " age=" + age / 1000 + "s screen=" + screenOn);
        if (!"info".equals(severity) && screenOn) {
            Banner.show(c, Jarvis.theme(c), severity, alert.optString("title"), alert.optString("message"));
        }
    }
}
