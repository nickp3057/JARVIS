package com.tvhome;

import android.content.Context;
import android.content.Intent;
import android.content.SharedPreferences;
import android.os.SystemClock;
import android.util.Log;

import org.json.JSONObject;

import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.net.HttpURLConnection;
import java.net.Inet4Address;
import java.net.InetAddress;
import java.net.NetworkInterface;
import java.net.URL;
import java.nio.charset.StandardCharsets;
import java.util.Collections;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicReference;

/**
 * JARVIS Core client. The TV holds its own scoped token (events:read,
 * status:read; set by tv/provision.sh through ProvisionReceiver), never the
 * admin token. The last good snapshot is kept so the home screen is never
 * blank when Core is down. The Optiplex's DHCP address drifts, so when Core
 * stops answering the TV scans its own /24 for it.
 */
final class Jarvis {
    private static final String TAG = "TvHomeJarvis";
    static final String ACTION_UPDATED = "com.tvhome.GLANCE_UPDATED";
    private static final String PREFS = "jarvis";
    private static final int PORT = 8000;
    private static final long DISCOVERY_COOLDOWN_MS = 5 * 60 * 1000;
    private static long lastDiscovery = -DISCOVERY_COOLDOWN_MS;

    private Jarvis() {}

    static SharedPreferences prefs(Context c) {
        return c.getSharedPreferences(PREFS, Context.MODE_PRIVATE);
    }

    // --- Settings Nick changes on the TV (SettingsActivity) -------------------

    static final String THEME = "theme_override", SPEAK = "speak_replies",
            REFRESH = "nightly_refresh", DOWNLOADS = "show_downloads";

    static boolean flag(Context c, String key) {
        return prefs(c).getBoolean(key, !SPEAK.equals(key)); // everything on but read-aloud
    }

    /** The TV's theme: Nick's pick on the TV, else Core's JARVIS_THEME. */
    static Theme theme(Context c) {
        String pick = prefs(c).getString(THEME, "");
        if (!pick.isEmpty()) return Theme.named(pick);
        JSONObject g = cached(c);
        return Theme.named(g != null ? g.optString("theme") : "");
    }

    static boolean provisioned(Context c) {
        return !prefs(c).getString("token", "").isEmpty();
    }

    static String base(Context c) {
        return prefs(c).getString("core", "");
    }

    static String token(Context c) {
        return prefs(c).getString("token", "");
    }

    static void provision(Context c, String core, String token) {
        prefs(c).edit().putString("core", core.replaceAll("/+$", "")).putString("token", token)
                .remove("glance").apply();
    }

    /** Last snapshot from Core, or null if there has never been one. */
    static JSONObject cached(Context c) {
        String raw = prefs(c).getString("glance", null);
        if (raw == null) return null;
        try {
            return new JSONObject(raw);
        } catch (Exception e) {
            return null;
        }
    }

    /** Wall-clock ms of the last successful fetch; 0 if none. */
    static long fetchedAt(Context c) {
        return prefs(c).getLong("fetched_at", 0);
    }

    /** Wall-clock ms of the last failed fetch since the last success; 0 if none. */
    static long failedAt(Context c) {
        return prefs(c).getLong("failed_at", 0);
    }

    /** Blocking: fetch /api/glance, cache it and tell the home screen. */
    static JSONObject refresh(Context c) throws Exception {
        if (!provisioned(c)) throw new IllegalStateException("not provisioned");
        try {
            return store(c, getJson(base(c), "/api/glance", token(c), 8000));
        } catch (IOException e) {
            String found = discover(c);
            if (found == null || found.equals(base(c))) {
                prefs(c).edit().putLong("failed_at", System.currentTimeMillis()).apply();
                notifyUpdated(c);
                throw e;
            }
            Log.i(TAG, "Core moved to " + found);
            prefs(c).edit().putString("core", found).apply();
            return store(c, getJson(found, "/api/glance", token(c), 8000));
        }
    }

    static void notifyUpdated(Context c) {
        c.sendBroadcast(new Intent(ACTION_UPDATED).setPackage(c.getPackageName()));
    }

    private static JSONObject store(Context c, JSONObject glance) {
        prefs(c).edit().putString("glance", glance.toString())
                .putLong("fetched_at", System.currentTimeMillis()).remove("failed_at").apply();
        notifyUpdated(c);
        return glance;
    }

    static HttpURLConnection open(String base, String path, String token, int readTimeoutMs)
            throws IOException {
        HttpURLConnection conn = (HttpURLConnection) new URL(base + path).openConnection();
        conn.setConnectTimeout(3000);
        conn.setReadTimeout(readTimeoutMs);
        conn.setRequestProperty("Authorization", "Bearer " + token);
        return conn;
    }

    private static JSONObject getJson(String base, String path, String token, int timeoutMs)
            throws Exception {
        HttpURLConnection conn = open(base, path, token, timeoutMs);
        conn.setRequestProperty("Accept", "application/json");
        try {
            int code = conn.getResponseCode();
            if (code != 200) throw new IOException("HTTP " + code + " from " + base + path);
            try (InputStream in = conn.getInputStream()) {
                ByteArrayOutputStream out = new ByteArrayOutputStream();
                byte[] buf = new byte[8192];
                for (int n; (n = in.read(buf)) > 0; ) out.write(buf, 0, n);
                return new JSONObject(out.toString(StandardCharsets.UTF_8.name()));
            }
        } finally {
            conn.disconnect();
        }
    }

    /** Blocking: Core's base URL on this LAN, found by asking every /24 neighbour. */
    static synchronized String discover(Context c) {
        long now = SystemClock.elapsedRealtime();
        if (now - lastDiscovery < DISCOVERY_COOLDOWN_MS) return null;
        lastDiscovery = now;
        String prefix = lanPrefix();
        if (prefix == null) return null;
        String token = token(c);
        AtomicReference<String> found = new AtomicReference<>();
        ExecutorService pool = Executors.newFixedThreadPool(32);
        for (int i = 1; i < 255; i++) {
            String candidate = "http://" + prefix + i + ":" + PORT;
            pool.execute(() -> {
                if (found.get() != null) return;
                try {
                    HttpURLConnection conn = open(candidate, "/api/glance", token, 4000);
                    conn.setConnectTimeout(400);
                    if (conn.getResponseCode() == 200) found.compareAndSet(null, candidate);
                    conn.disconnect();
                } catch (Exception ignored) {
                }
            });
        }
        pool.shutdown();
        try {
            pool.awaitTermination(30, TimeUnit.SECONDS);
        } catch (InterruptedException ignored) {
        }
        pool.shutdownNow();
        Log.i(TAG, "discovery on " + prefix + "0/24: " + found.get());
        return found.get();
    }

    private static String lanPrefix() {
        try {
            for (NetworkInterface nic : Collections.list(NetworkInterface.getNetworkInterfaces())) {
                if (!nic.isUp() || nic.isLoopback()) continue;
                for (InetAddress a : Collections.list(nic.getInetAddresses())) {
                    if (a instanceof Inet4Address && a.isSiteLocalAddress()) {
                        String ip = a.getHostAddress();
                        return ip.substring(0, ip.lastIndexOf('.') + 1);
                    }
                }
            }
        } catch (Exception ignored) {
        }
        return null;
    }
}
