package com.tvhome;

import android.content.ComponentName;
import android.content.Context;
import android.media.MediaMetadata;
import android.media.session.MediaController;
import android.media.session.MediaSessionManager;
import android.media.session.PlaybackState;
import android.os.Handler;
import android.os.HandlerThread;
import android.os.SystemClock;
import android.util.Log;

import org.json.JSONObject;

import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.nio.charset.StandardCharsets;
import java.util.List;

/**
 * Light sync: tells Core where Plex is in the film ({@code POST /api/tv/playback},
 * token scope playback:write), on every playback change and every second
 * while playing (every 5 s while paused). Plex's media session is read
 * through MediaListener's grant, like Music. The position is advanced to the
 * moment of sending; Core stamps the arrival.
 *
 * A 403 (the token hasn't been granted playback:write yet) or a 401 backs off
 * quietly for 5 minutes; Core being down, 30 s. Never spams.
 */
final class PlaybackReport {
    private static final String TAG = "TvHomePlayback";
    private static final String PLEX = "com.plexapp.android";
    private static final long PLAYING_MS = 1000, PAUSED_MS = 5000;
    private static final long NOT_ALLOWED_BACKOFF_MS = 5 * 60 * 1000, DOWN_BACKOFF_MS = 30_000;

    private static PlaybackReport instance;

    private final Context context;
    private final Handler handler;
    private final ComponentName listener;
    private final Callback callback = new Callback(this);
    private final Runnable tick = this::tick;
    private MediaController plex;
    private long blockedUntil;
    private String lastSent = "";

    private PlaybackReport(Context c) {
        context = c.getApplicationContext();
        HandlerThread thread = new HandlerThread("playback-report");
        thread.start();
        handler = new Handler(thread.getLooper());
        listener = new ComponentName(context, MediaListener.class);
    }

    /** Once per process (RemoteBridgeService). */
    static synchronized void start(Context c) {
        if (instance != null) return;
        instance = new PlaybackReport(c);
        instance.handler.post(instance::attach);
    }

    /** Watch the active sessions; retry every 5 min until the listener grant exists. */
    private void attach() {
        try {
            MediaSessionManager msm = context.getSystemService(MediaSessionManager.class);
            msm.addOnActiveSessionsChangedListener(this::sessionsChanged, listener, handler);
            sessionsChanged(msm.getActiveSessions(listener));
        } catch (SecurityException e) {
            Log.w(TAG, "no media session access yet (notification listener not enabled)");
            handler.postDelayed(this::attach, NOT_ALLOWED_BACKOFF_MS);
        }
    }

    private void sessionsChanged(List<MediaController> sessions) {
        MediaController found = null;
        if (sessions != null) {
            for (MediaController m : sessions) {
                if (PLEX.equals(m.getPackageName())) {
                    found = m;
                    break;
                }
            }
        }
        if (plex != null && (found == null || !plex.getSessionToken().equals(found.getSessionToken()))) {
            plex.unregisterCallback(callback);
            if (found == null) send(null);  // Plex closed: say so once
        }
        if (found != null && (plex == null || !plex.getSessionToken().equals(found.getSessionToken()))) {
            found.registerCallback(callback, handler);
        }
        plex = found;
        changed();
    }

    /** A state or metadata change: report now, then keep the heartbeat going. */
    void changed() {
        handler.removeCallbacks(tick);
        tick();
    }

    private void tick() {
        if (plex == null) return;
        PlaybackState state = plex.getPlaybackState();
        send(state);
        if (state == null) return;
        int s = state.getState();
        if (s == PlaybackState.STATE_PLAYING) {
            handler.postDelayed(tick, PLAYING_MS);
        } else if (s == PlaybackState.STATE_PAUSED || s == PlaybackState.STATE_BUFFERING) {
            handler.postDelayed(tick, PAUSED_MS);
        }
    }

    private static String stateName(PlaybackState state) {
        if (state == null) return "stopped";
        switch (state.getState()) {
            case PlaybackState.STATE_PLAYING: return "playing";
            case PlaybackState.STATE_PAUSED: return "paused";
            case PlaybackState.STATE_BUFFERING:
            case PlaybackState.STATE_CONNECTING: return "buffering";
            default: return "stopped";
        }
    }

    private void send(PlaybackState state) {
        if (!Jarvis.provisioned(context)) return;
        String name = stateName(state);
        long now = SystemClock.elapsedRealtime();
        if (now < blockedUntil) return;
        if ("stopped".equals(name) && "stopped".equals(lastSent)) return;  // once is enough
        long position = 0;
        float speed = 0;
        if (state != null) {
            position = state.getPosition();
            speed = state.getPlaybackSpeed();
            if (state.getState() == PlaybackState.STATE_PLAYING && state.getLastPositionUpdateTime() > 0) {
                position += (long) ((now - state.getLastPositionUpdateTime()) * speed);
            }
        }
        String title = "", key = "";
        MediaMetadata meta = plex != null ? plex.getMetadata() : null;
        if (meta != null) {
            String t = meta.getString(MediaMetadata.METADATA_KEY_TITLE);
            String id = meta.getString(MediaMetadata.METADATA_KEY_MEDIA_ID);
            if (t != null) title = t;
            if (id != null && id.matches("\\d{1,12}")) key = id;
        }
        try {
            JSONObject body = new JSONObject()
                    .put("package", PLEX).put("state", name)
                    .put("position_ms", Math.max(0, position)).put("speed", Math.max(0, speed))
                    .put("title", title);
            if (!key.isEmpty()) body.put("rating_key", key);
            HttpURLConnection conn = Jarvis.open(Jarvis.base(context), "/api/tv/playback", Jarvis.token(context), 2000);
            conn.setConnectTimeout(1500);
            conn.setRequestMethod("POST");
            conn.setDoOutput(true);
            conn.setRequestProperty("Content-Type", "application/json");
            try {
                try (OutputStream out = conn.getOutputStream()) {
                    out.write(body.toString().getBytes(StandardCharsets.UTF_8));
                }
                int code = conn.getResponseCode();
                if (code == 401 || code == 403) {
                    Log.i(TAG, "Core won't take playback yet (HTTP " + code + "); trying again in 5 min");
                    blockedUntil = now + NOT_ALLOWED_BACKOFF_MS;
                } else if (code == 200) {
                    lastSent = name;
                }
            } finally {
                conn.disconnect();
            }
        } catch (Exception e) {
            blockedUntil = now + DOWN_BACKOFF_MS;  // Core down or moved: Jarvis.refresh finds it again
        }
    }

    /** Static (no anonymous classes: d8 34 can't read javac 24's inner classes). */
    private static final class Callback extends MediaController.Callback {
        private final PlaybackReport owner;

        Callback(PlaybackReport owner) {
            this.owner = owner;
        }

        @Override
        public void onPlaybackStateChanged(PlaybackState state) {
            owner.changed();
        }

        @Override
        public void onMetadataChanged(MediaMetadata metadata) {
            owner.changed();
        }

        @Override
        public void onSessionDestroyed() {
            owner.sessionsChanged(null);
        }
    }
}
