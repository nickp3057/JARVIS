package com.tvhome;

import android.content.Context;
import android.speech.tts.TextToSpeech;
import android.util.Log;

import org.json.JSONObject;

import java.io.ByteArrayOutputStream;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.nio.charset.StandardCharsets;
import java.text.SimpleDateFormat;
import java.util.Date;
import java.util.Locale;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/**
 * "Jarvis, ..." on the remote's mic. The Assistant still hears it (the remote
 * mic belongs to Google), so RemoteBridgeService catches the transcript from
 * the log, this dismisses the Assistant's panel and sends the rest to Core's
 * POST /chat (token scope "chat"). The answer shows as a banner and is read
 * aloud. One Core session per day, so follow-ups have context.
 */
final class Voice {
    private static final String TAG = "TvHomeVoice";
    /** Wake word plus what the recogniser tends to hear instead. */
    private static final Pattern WAKE = Pattern.compile(
            "^(?:hey |ok |okay )?(?:jarvis|jervis|travis|charvis|jarvas)\\b[,.!?]?\\s*(.*)$",
            Pattern.CASE_INSENSITIVE);
    private static TextToSpeech tts;
    private static boolean ttsReady;

    private Voice() {}

    /** The request after the wake word, or null if this speech isn't for JARVIS. */
    static String request(String speech) {
        Matcher m = WAKE.matcher(speech.trim());
        if (!m.find()) return null;
        String rest = m.group(1).trim();
        return rest.isEmpty() ? null : rest;
    }

    /** Blocking; call off the main thread. */
    static void ask(Context c, String request) {
        Theme theme = Theme.named(themeName(c));
        dismissAssistant(c);
        Banner.show(c, theme, "reply", "“" + request + "”", "One moment, sir…");
        String reply;
        try {
            reply = chat(c, request);
        } catch (SecurityException e) {
            Banner.show(c, theme, "warning", "JARVIS can't hear this TV yet",
                    "Re-run tv/provision.sh to give the TV the chat scope.");
            return;
        } catch (Exception e) {
            Log.w(TAG, "chat failed", e);
            Banner.show(c, theme, "warning", "Core didn't answer", String.valueOf(e.getMessage()));
            return;
        }
        Banner.show(c, theme, "reply", "“" + request + "”", reply);
        speak(c, reply);
    }

    private static String chat(Context c, String request) throws Exception {
        if (!Jarvis.provisioned(c)) throw new SecurityException("not provisioned");
        String today = new SimpleDateFormat("yyyy-MM-dd", Locale.US).format(new Date());
        String session = today.equals(Jarvis.prefs(c).getString("chat_day", ""))
                ? Jarvis.prefs(c).getString("chat_session", null) : null;
        JSONObject body = new JSONObject().put("message", request);
        if (session != null) body.put("session_id", session);
        HttpURLConnection conn = Jarvis.open(Jarvis.base(c), "/chat", Jarvis.token(c), 90_000);
        conn.setRequestMethod("POST");
        conn.setDoOutput(true);
        conn.setRequestProperty("Content-Type", "application/json");
        try {
            try (OutputStream out = conn.getOutputStream()) {
                out.write(body.toString().getBytes(StandardCharsets.UTF_8));
            }
            int code = conn.getResponseCode();
            if (code == 401 || code == 403) throw new SecurityException("HTTP " + code);
            if (code == 404 && session != null) {
                Jarvis.prefs(c).edit().remove("chat_session").apply();
                return chat(c, request); // the session was deleted; start a new one
            }
            if (code != 200) throw new java.io.IOException("HTTP " + code);
            JSONObject reply;
            try (InputStream in = conn.getInputStream()) {
                ByteArrayOutputStream buf = new ByteArrayOutputStream();
                byte[] b = new byte[8192];
                for (int n; (n = in.read(b)) > 0; ) buf.write(b, 0, n);
                reply = new JSONObject(buf.toString(StandardCharsets.UTF_8.name()));
            }
            Jarvis.prefs(c).edit().putString("chat_day", today)
                    .putString("chat_session", reply.optString("session_id")).apply();
            return plain(reply.optString("reply"));
        } finally {
            conn.disconnect();
        }
    }

    /** Markdown out, so the banner and the voice read naturally. */
    private static String plain(String text) {
        return text.replaceAll("[*_`#>]+", "").replaceAll("\\[(.*?)\\]\\(.*?\\)", "$1")
                .replaceAll("\\s+", " ").trim();
    }

    /** Close the Assistant's panel if it is the focused window (never "Back" out of Plex). */
    private static void dismissAssistant(Context c) {
        try (SelfAdb adb = SelfAdb.connect(c, 10_000)) {
            adb.shell("dumpsys window | grep -q 'mCurrentFocus.*katniss' && input keyevent 4");
        } catch (Exception e) {
            Log.i(TAG, "couldn't dismiss the Assistant: " + e);
        }
    }

    private static synchronized void speak(Context c, String text) {
        if (tts == null) {
            tts = new TextToSpeech(c.getApplicationContext(), status -> {
                synchronized (Voice.class) {
                    ttsReady = status == TextToSpeech.SUCCESS;
                    if (ttsReady) {
                        tts.setLanguage(Locale.UK);
                        tts.speak(text, TextToSpeech.QUEUE_FLUSH, null, "jarvis");
                    }
                }
            });
            return;
        }
        if (ttsReady) tts.speak(text, TextToSpeech.QUEUE_FLUSH, null, "jarvis");
    }

    private static String themeName(Context c) {
        JSONObject g = Jarvis.cached(c);
        return g != null ? g.optString("theme") : "";
    }
}
