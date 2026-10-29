package com.tvhome;

import android.content.Context;
import android.util.Log;

import org.json.JSONObject;

import java.io.BufferedReader;
import java.io.InputStreamReader;
import java.net.HttpURLConnection;
import java.nio.charset.StandardCharsets;

/**
 * Follows Core's room stream (GET /api/home/stream, server-sent events),
 * whose every event carries the shared theme. A change (Barn Door engaged
 * or stood down, a pick on the phone) is followed at once instead of on the
 * home screen's 5 s theme poll; HomeActivity recreates on the update
 * broadcast. An older Core without the stream just leaves the poll.
 */
final class ThemeStream {
    private static final String TAG = "TvHomeTheme";

    private ThemeStream() {}

    static void start(Context context) {
        Thread t = new Thread(() -> loop(context.getApplicationContext()), "theme-stream");
        t.setDaemon(true);
        t.start();
    }

    private static void loop(Context c) {
        while (true) {
            if (Jarvis.provisioned(c)) {
                try {
                    follow(c);
                } catch (Exception e) {
                    Log.i(TAG, "stream dropped: " + e);
                }
            }
            try {
                Thread.sleep(5_000);
            } catch (InterruptedException e) {
                return;
            }
        }
    }

    private static void follow(Context c) throws Exception {
        // Core keeps a keep-alive every 15 s, so 45 s of silence means it's gone.
        HttpURLConnection conn = Jarvis.open(Jarvis.base(c), "/api/home/stream", Jarvis.token(c), 45_000);
        conn.setRequestProperty("Accept", "text/event-stream");
        try {
            int code = conn.getResponseCode();
            if (code != 200) throw new java.io.IOException("HTTP " + code);
            BufferedReader in = new BufferedReader(
                    new InputStreamReader(conn.getInputStream(), StandardCharsets.UTF_8));
            String line;
            while ((line = in.readLine()) != null) {
                if (!line.startsWith("data:")) continue;
                String shared = new JSONObject(line.substring(5).trim()).optString("theme");
                JSONObject g = Jarvis.cached(c);
                if (shared.isEmpty() || g == null || shared.equals(g.optString("theme"))) continue;
                if (Jarvis.followTheme(c)) Jarvis.notifyUpdated(c);
            }
        } finally {
            conn.disconnect();
        }
    }
}
