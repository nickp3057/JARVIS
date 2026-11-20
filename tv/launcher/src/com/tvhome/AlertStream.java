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
 * {@code event: card} frames are answer screens ({@link AnswerCard}).
 * Each connection replays Core's recent history (ids restart at 1 whenever
 * Core restarts, so a remembered id can't be trusted); only alerts under two
 * minutes old count, and each one only once.
 */
final class AlertStream {
    private static final String TAG = "TvHomeAlerts";
    private static final long FRESH_MS = 2 * 60 * 1000;
    /** "id@created_at" of fresh alerts already handled, across reconnects. */
    private static final java.util.Set<String> SEEN = new java.util.HashSet<>();
    /** The open stream, so {@link #kick} can drop it. */
    private static volatile HttpURLConnection current;
    private static final Object NAP = new Object();
    private static volatile boolean kicked;

    private AlertStream() {}

    static void start(Context context) {
        Thread t = new Thread(() -> loop(context.getApplicationContext()), "alerts");
        t.setDaemon(true);
        t.start();
    }

    /**
     * Reconnect now. In standby the TV's Wi-Fi drops and comes back, leaving
     * a dead socket that takes up to 45 s to notice: on 2026-11-19 Core
     * powered the TV on at 7:00:16 but the wake_up went into a dead stream,
     * and the replay only arrived at 7:01:25. Called when the screen turns
     * on and when Wi-Fi comes back; the fresh connection replays the
     * wake-up (and any I'm up) at once.
     */
    static void kick(String why) {
        Log.i(TAG, "reconnecting: " + why);
        kicked = true;
        HttpURLConnection conn = current;
        if (conn != null) {
            new Thread(conn::disconnect, "alerts-kick").start();  // not on the main thread
        }
        synchronized (NAP) {
            NAP.notifyAll();
        }
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
            if (kicked) {
                kicked = false;
                backoff = 5_000;
                continue;
            }
            try {
                synchronized (NAP) {
                    NAP.wait(backoff);
                }
            } catch (InterruptedException e) {
                return;
            }
            if (kicked) {
                kicked = false;
                backoff = 5_000;
                continue;
            }
            backoff = Math.min(backoff * 2, 60_000);
        }
    }

    private static void follow(Context c) throws Exception {
        // Core keeps a keep-alive every 15 s, so 45 s of silence means it's gone.
        HttpURLConnection conn = Jarvis.open(Jarvis.base(c), "/api/clients/events?since=0",
                Jarvis.token(c), 45_000);
        conn.setRequestProperty("Accept", "text/event-stream");
        current = conn;
        kicked = false;
        try {
            int code = conn.getResponseCode();
            if (code != 200) throw new java.io.IOException("HTTP " + code);
            Log.i(TAG, "following " + Jarvis.base(c));
            BufferedReader in = new BufferedReader(
                    new InputStreamReader(conn.getInputStream(), StandardCharsets.UTF_8));
            StringBuilder data = new StringBuilder();
            String event = "";
            String line;
            while ((line = in.readLine()) != null) {
                if (line.startsWith("data:")) {
                    data.append(line.substring(5).trim());
                } else if (line.startsWith("event:")) {
                    event = line.substring(6).trim();
                } else if (line.isEmpty()) {
                    if (data.length() > 0) {
                        JSONObject body = new JSONObject(data.toString());
                        if ("card".equals(event)) card(c, body);
                        else handle(c, body);
                        data.setLength(0);
                    }
                    event = "";
                }
            }
        } finally {
            current = null;
            conn.disconnect();
        }
    }

    /**
     * An answer screen (Core's brain/answer_cards.py): shown on the right
     * for what's left of its time, once, and only with the screen on. Card
     * ids are their own series, so they're remembered apart from alerts.
     */
    private static void card(Context c, JSONObject card) {
        if ("tv_play".equals(card.optString("card"))) {
            play(c, card);
            return;
        }
        if (!AnswerCard.known(card.optString("card"))) return;
        long ttl = (long) (card.optDouble("ttl_s", 60) * 1000);
        long age = System.currentTimeMillis() - (long) (card.optDouble("created_at") * 1000);
        if (age >= ttl) return;
        synchronized (SEEN) {
            if (!SEEN.add("card" + card.optLong("id") + "@" + card.optDouble("created_at"))) return;
        }
        if (!c.getSystemService(PowerManager.class).isInteractive()) return;
        Log.i(TAG, "card " + card.optLong("id") + " [" + card.optString("card") + "] age=" + age / 1000 + "s");
        // A skewed clock still gets a few seconds on screen.
        AnswerCard.show(c, Jarvis.theme(c), card, Math.max(8_000, ttl - Math.max(0, age)));
    }

    /**
     * Core asks the TV to play a Plex title (light sync calibration's test
     * clip): once, while fresh, screen on or off.
     */
    private static void play(Context c, JSONObject card) {
        long ttl = (long) (card.optDouble("ttl_s", 20) * 1000);
        long age = System.currentTimeMillis() - (long) (card.optDouble("created_at") * 1000);
        if (age >= ttl) return;
        synchronized (SEEN) {
            if (!SEEN.add("card" + card.optLong("id") + "@" + card.optDouble("created_at"))) return;
        }
        JSONObject data = card.optJSONObject("data");
        if (data == null || data.optString("rating_key").isEmpty()) return;
        Log.i(TAG, "play " + data.optString("title") + " for Core");
        String type = data.optString("type", "movie");
        String title = data.optString("title"), key = data.optString("rating_key");
        new Thread(() -> PlexWatch.playFresh(c, type, title, key), "tv-play").start();
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
        String kind = alert.optString("kind");
        if ("wake_snooze".equals(kind) || "wake_dismiss".equals(kind)) {
            // Snooze / I'm up pressed on the phone. Core rings the snooze itself.
            JSONObject ctx = alert.optJSONObject("context");
            long until = ctx != null ? (long) (ctx.optDouble("until") * 1000) : 0;
            if ("wake_snooze".equals(kind)) Wake.snoozedByCore(c, until);
            else Wake.dismiss(c);
            return;
        }
        if (wake) {
            // Replays after a restart or reboot must not wake him twice; the
            // in-memory SEEN set doesn't survive those, so remember it on disk.
            long at = (long) alert.optDouble("created_at");
            if (at <= Jarvis.prefs(c).getLong("last_wake_at", 0)) return;
            Jarvis.prefs(c).edit().putLong("last_wake_at", at).commit();
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
