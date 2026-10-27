package com.tvhome;

import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.Service;
import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;
import android.content.IntentFilter;
import android.net.Uri;
import android.os.IBinder;
import android.util.Log;

import org.json.JSONArray;
import org.json.JSONObject;

import java.io.BufferedReader;
import java.io.ByteArrayOutputStream;
import java.io.InputStream;
import java.io.InputStreamReader;
import java.net.HttpURLConnection;
import java.net.URL;
import java.net.URLEncoder;
import java.nio.charset.StandardCharsets;
import java.util.Locale;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/**
 * Watches the system log for two things and reacts:
 *  - the Assistant's final speech transcript (remote mic), or the query it hands
 *    to YouTube (Google Home / Nest requests): if it names a movie or show in
 *    the Plex library, open that title in Plex;
 *  - remote app-shortcut buttons (Netflix, Prime, ...): send them to Plex/YouTube;
 *  - Plex coming to the foreground: sign in past its profile PIN screen.
 * Needs READ_LOGS and SYSTEM_ALERT_WINDOW, both granted over adb. Also runs the
 * post-boot bloat cleanup once per boot, follows JARVIS Core's alert stream
 * (AlertStream) and arms the nightly audio refresh (NightlyRefresh).
 */
public class RemoteBridgeService extends Service {
    private static final String TAG = "TvHomeBridge";
    private static final String PLEX = "com.plexapp.android";
    private static final String YOUTUBE = "com.google.android.youtube.tv";

    private static final Pattern SPEECH = Pattern.compile(
            "state\\[FINAL_RESULT\\] : result\\[recognized_speech: \"(.*?)\"");
    private static final Pattern YOUTUBE_VOICE = Pattern.compile(
            "DeepLink: \"\\S*[?&]launch=voice&vq=([^&\"]+)");
    private static final Pattern PLAY_VERB = Pattern.compile(
            "^\\s*(play|watch|put on|start|resume)\\b");
    private static final Pattern PLEX_START = Pattern.compile(
            "START u0 \\{.*cmp=com\\.plexapp\\.android/");
    private static final Pattern KEY = Pattern.compile(
            "handleKeyBeforeQueueing event=KeyEvent \\{ action=ACTION_DOWN, keyCode=(\\w+),");
    private static final Pattern FILLER = Pattern.compile(
            "^(play|watch|open|put on|start|search for|search|find|show me|resume)\\s+|\\s+(on|in) (plex|(the )?[a-z0-9 ]*tv)$");

    private static boolean cleanupStarted;

    private Thread reader;

    static void start(Context context) {
        context.startForegroundService(new Intent(context, RemoteBridgeService.class));
    }

    @Override
    public int onStartCommand(Intent intent, int flags, int startId) {
        NotificationManager nm = getSystemService(NotificationManager.class);
        nm.createNotificationChannel(new NotificationChannel(
                "bridge", "Remote bridge", NotificationManager.IMPORTANCE_MIN));
        startForeground(1, new Notification.Builder(this, "bridge")
                .setContentTitle("JARVIS")
                .setSmallIcon(android.R.drawable.ic_btn_speak_now)
                .build());
        // TCL's boot manager may block the BOOT_COMPLETED start, so clean up on
        // whichever start comes first after boot (usually from HomeActivity).
        if (!cleanupStarted) {
            cleanupStarted = true;
            new Thread(() -> BootCleanup.run(this), "cleanup").start();
            // At boot Plex is launched before this service starts listening.
            new Thread(() -> PlexAutoLogin.run(this), "login").start();
            AlertStream.start(this);
            NightlyRefresh.scheduleNext(this);
            registerReceiver(new ScreenOffReceiver(), new IntentFilter(Intent.ACTION_SCREEN_OFF));
        }
        if (reader == null || !reader.isAlive()) {
            reader = new Thread(this::readLog, "logcat");
            reader.start();
        }
        return START_STICKY;
    }

    /** A minute after the TV is switched off, refresh it if it has been up long (NightlyRefresh). */
    private static final class ScreenOffReceiver extends BroadcastReceiver {
        @Override
        public void onReceive(Context context, Intent intent) {
            Context app = context.getApplicationContext();
            new Thread(() -> {
                try {
                    Thread.sleep(60_000);
                } catch (InterruptedException e) {
                    return;
                }
                NightlyRefresh.refreshIfOff(app, "screen off",
                        NightlyRefresh.SCREEN_OFF_UPTIME_MS, false);
            }, "screen-off").start();
        }
    }

    @Override
    public IBinder onBind(Intent intent) {
        return null;
    }

    private void readLog() {
        while (true) {
            try {
                Process p = new ProcessBuilder("logcat", "-v", "brief", "-T", "1",
                        "katniss_interactor_KatnissConversationStateListenerImpl:I",
                        "WindowManager:D", "cobalt:I", "ActivityTaskManager:I", "*:S").redirectErrorStream(true).start();
                BufferedReader in = new BufferedReader(new InputStreamReader(p.getInputStream()));
                String line;
                while ((line = in.readLine()) != null) {
                    Matcher m = SPEECH.matcher(line);
                    if (m.find()) {
                        String speech = m.group(1);
                        new Thread(() -> handleSpeech(speech)).start();
                        continue;
                    }
                    m = YOUTUBE_VOICE.matcher(line);
                    if (m.find()) {
                        String speech = Uri.decode(m.group(1).replace('+', ' '));
                        new Thread(() -> handleSpeech(speech)).start();
                        continue;
                    }
                    if (PLEX_START.matcher(line).find()) {
                        new Thread(() -> PlexAutoLogin.run(this), "login").start();
                        continue;
                    }
                    m = KEY.matcher(line);
                    if (m.find()) handleKey(m.group(1));
                }
            } catch (Exception e) {
                Log.w(TAG, "log reader stopped", e);
            }
            try {
                Thread.sleep(2000);
            } catch (InterruptedException e) {
                return;
            }
        }
    }

    private void handleKey(String keyCode) {
        String target = Config.KEY_TARGETS.get(keyCode);
        if (target == null) return;
        Log.i(TAG, "key " + keyCode + " -> " + target);
        launchPackage(target);
    }

    private void handleSpeech(String speech) {
        String query = normalize(speech);
        Log.i(TAG, "heard \"" + speech + "\" -> query \"" + query + "\"");
        if (query.isEmpty()) return;
        try {
            JSONObject best;
            try {
                best = findTitle(query);
            } catch (java.io.IOException e) {
                // The NAS can be slow to answer the first request after its disks spin down.
                Log.i(TAG, "search failed (" + e + "), retrying");
                best = findTitle(query);
            }
            if (best == null) {
                Log.i(TAG, "no Plex match; leaving it to the Assistant");
                return;
            }
            String type = best.getString("type");
            String title = best.getString("title");
            // The app's own library route (watch.plex.tv/<type>/<slug> is the
            // Discover catalog page, which can't play the NAS copy).
            String url = "https://watch.plex.tv/libraries/" + Config.PLEX_SERVER_ID + "/" + type
                    + "/" + slugify(title) + "/" + best.getString("ratingKey");
            boolean play = PLAY_VERB.matcher(speech.toLowerCase(Locale.US)).find();
            Log.i(TAG, (play ? "playing " : "opening ") + title + " (" + url + ")");
            // Let the Assistant's overlay finish before taking over the screen.
            Thread.sleep(1200);
            Intent i = new Intent(Intent.ACTION_VIEW, Uri.parse(url));
            i.setPackage(PLEX);
            i.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
            startActivity(i);
            if (play) PlexAutoLogin.pressWatch(this);
        } catch (Exception e) {
            Log.w(TAG, "voice lookup failed", e);
        }
    }

    /** Best movie/show whose title matches the spoken query, or null. */
    private JSONObject findTitle(String query) throws Exception {
        JSONArray hubs = getJson(Config.PLEX_SERVER + "/hubs/search?limit=10&query="
                + URLEncoder.encode(query, "UTF-8"))
                .getJSONObject("MediaContainer").optJSONArray("Hub");
        if (hubs == null) return null;
        JSONObject best = null;
        int bestScore = 0;
        for (int h = 0; h < hubs.length(); h++) {
            JSONObject hub = hubs.getJSONObject(h);
            String type = hub.optString("type");
            if (!type.equals("movie") && !type.equals("show")) continue;
            JSONArray items = hub.optJSONArray("Metadata");
            if (items == null) continue;
            for (int i = 0; i < items.length(); i++) {
                JSONObject item = items.getJSONObject(i);
                if (!item.has("guid")) continue;
                int score = score(query, normalize(item.optString("title")));
                if (score > bestScore) {
                    bestScore = score;
                    best = item;
                }
            }
        }
        return best;
    }

    /** 3 = exact, 2 = title starts with query or query starts with title, 0 = no match. */
    private static int score(String query, String title) {
        String bare = title.replaceAll("\\s*\\(.*?\\)", "").trim();
        if (title.equals(query) || bare.equals(query)) return 3;
        if (bare.length() >= 3 && (bare.startsWith(query) || query.startsWith(bare))) return 2;
        return 0;
    }

    private static String slugify(String title) {
        return title.toLowerCase(Locale.US).replaceAll("[^a-z0-9]+", "-").replaceAll("^-|-$", "");
    }

    private static String normalize(String s) {
        String out = s.toLowerCase(Locale.US).replace("&", "and")
                .replaceAll("[^a-z0-9() ]", " ").replaceAll("\\s+", " ").trim();
        return FILLER.matcher(out).replaceAll("").trim();
    }

    private static JSONObject getJson(String url) throws Exception {
        HttpURLConnection c = (HttpURLConnection) new URL(url).openConnection();
        c.setConnectTimeout(4000);
        c.setReadTimeout(15000);
        c.setRequestProperty("Accept", "application/json");
        c.setRequestProperty("X-Plex-Token", Config.PLEX_TOKEN);
        try (InputStream in = c.getInputStream()) {
            ByteArrayOutputStream out = new ByteArrayOutputStream();
            byte[] buf = new byte[8192];
            for (int n; (n = in.read(buf)) > 0; ) out.write(buf, 0, n);
            return new JSONObject(out.toString(StandardCharsets.UTF_8.name()));
        } finally {
            c.disconnect();
        }
    }

    private void launchPackage(String pkg) {
        Intent i = getPackageManager().getLeanbackLaunchIntentForPackage(pkg);
        if (i == null) i = getPackageManager().getLaunchIntentForPackage(pkg);
        if (i == null) return;
        i.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
        startActivity(i);
    }
}
