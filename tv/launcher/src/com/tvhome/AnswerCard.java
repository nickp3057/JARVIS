package com.tvhome;

import android.content.Context;
import android.graphics.PixelFormat;
import android.graphics.Typeface;
import android.graphics.drawable.GradientDrawable;
import android.os.Handler;
import android.os.Looper;
import android.text.TextUtils;
import android.view.Gravity;
import android.view.View;
import android.view.WindowManager;
import android.widget.LinearLayout;
import android.widget.TextView;

import org.json.JSONArray;
import org.json.JSONObject;

import java.text.SimpleDateFormat;
import java.util.Date;
import java.util.Locale;

/**
 * Answer screens (Core's brain/answer_cards.py): when Nick asks JARVIS
 * something with a visual shape ("what's the weather?", "what's my day look
 * like?", "what timers have I got?"), Core sends an {@code event: card} on
 * the alert stream and this draws it on the right of the screen, over any
 * app, without taking focus. It clears itself after the card's time (a
 * minute); a new card replaces it. One renderer per card type; an unknown
 * type (a newer Core) is ignored. Calm: a fade in and out, no pulsing.
 */
final class AnswerCard {
    private static final Handler MAIN = new Handler(Looper.getMainLooper());
    private static final int WIDTH_DP = 540;
    private static View current;
    /** Ticks the timers card's countdowns once a second while it's up. */
    private static Runnable ticker;

    private AnswerCard() {}

    /** True if this card type has a renderer here. */
    static boolean known(String type) {
        return "weather_today".equals(type) || "calendar_day".equals(type) || "timers".equals(type);
    }

    static void show(Context context, Theme theme, JSONObject card, long showMs) {
        MAIN.post(() -> {
            Context c = context.getApplicationContext();
            WindowManager wm = c.getSystemService(WindowManager.class);
            dismiss(wm);
            float d = c.getResources().getDisplayMetrics().density;
            String type = card.optString("card");
            JSONObject data = card.optJSONObject("data");
            if (data == null) data = new JSONObject();

            LinearLayout box = new LinearLayout(c);
            box.setOrientation(LinearLayout.VERTICAL);
            box.setPadding(px(d, 24), px(d, 20), px(d, 24), px(d, 22));
            GradientDrawable bg = new GradientDrawable();
            bg.setColor(Theme.alpha(theme.raised, 245));
            bg.setCornerRadius(px(d, 18));
            bg.setStroke(px(d, 1), Theme.alpha(theme.accent, 120));
            box.setBackground(bg);

            TextView eyebrow = label(c, "JARVIS", 11, Theme.alpha(theme.accent, 230));
            eyebrow.setLetterSpacing(0.25f);
            box.addView(eyebrow);
            TextView title = label(c, card.optString("title"), 20, theme.text);
            title.setTypeface(Typeface.create("sans-serif-medium", Typeface.NORMAL));
            title.setPadding(0, 0, 0, px(d, 14));
            box.addView(title);

            long sentAt = (long) (card.optDouble("created_at") * 1000);
            if ("weather_today".equals(type)) weather(c, d, theme, box, data);
            else if ("calendar_day".equals(type)) calendar(c, d, theme, box, data);
            else timers(c, d, theme, box, data, sentAt);

            WindowManager.LayoutParams lp = new WindowManager.LayoutParams(
                    px(d, WIDTH_DP), WindowManager.LayoutParams.WRAP_CONTENT,
                    WindowManager.LayoutParams.TYPE_APPLICATION_OVERLAY,
                    WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE
                            | WindowManager.LayoutParams.FLAG_NOT_TOUCHABLE,
                    PixelFormat.TRANSLUCENT);
            lp.gravity = Gravity.END | Gravity.CENTER_VERTICAL;
            lp.x = px(d, 32);
            try {
                wm.addView(box, lp);
            } catch (Exception e) {
                return; // overlay permission revoked
            }
            current = box;
            box.setAlpha(0f);
            box.animate().alpha(1f).setDuration(350).start();
            View shown = box;
            MAIN.postDelayed(() -> {
                if (current == shown) shown.animate().alpha(0f).setDuration(500)
                        .withEndAction(() -> dismiss(wm)).start();
            }, showMs);
        });
    }

    private static void dismiss(WindowManager wm) {
        if (ticker != null) {
            MAIN.removeCallbacks(ticker);
            ticker = null;
        }
        if (current == null) return;
        try {
            wm.removeView(current);
        } catch (Exception ignored) {
        }
        current = null;
    }

    // --- weather_today ------------------------------------------------------------

    private static void weather(Context c, float d, Theme t, LinearLayout box, JSONObject w) {
        LinearLayout now = row(c);
        now.setGravity(Gravity.CENTER_VERTICAL);
        TextView icon = label(c, icon(w.optString("condition"), w.optBoolean("is_day", true)), 40, t.text);
        icon.setPadding(0, 0, px(d, 14), 0);
        now.addView(icon);
        TextView temp = label(c, w.isNull("temp_f") ? "--" : w.optInt("temp_f") + "°", 52, t.text);
        temp.setTypeface(Typeface.create("sans-serif-thin", Typeface.NORMAL));
        temp.setPadding(0, 0, px(d, 16), 0);
        now.addView(temp);
        LinearLayout side = column(c);
        String desc = w.optString("description");
        if (!desc.isEmpty()) side.addView(label(c, Character.toUpperCase(desc.charAt(0)) + desc.substring(1), 16, t.text));
        if (!w.isNull("high_f") && !w.isNull("low_f")) {
            side.addView(label(c, "H " + w.optInt("high_f") + "°   L " + w.optInt("low_f") + "°", 15, t.dim));
        }
        now.addView(side);
        box.addView(now);

        JSONArray hours = w.optJSONArray("hours");
        if (hours != null && hours.length() > 0) {
            LinearLayout strip = row(c);
            strip.setPadding(0, px(d, 16), 0, px(d, 14));
            int shown = Math.min(hours.length(), 10);  // equal columns across the card
            SimpleDateFormat hourFormat = new SimpleDateFormat("ha", Locale.US);
            for (int i = 0; i < shown; i++) {
                JSONObject h = hours.optJSONObject(i);
                if (h == null) continue;
                int rain = h.optInt("rain_chance", 0);
                LinearLayout col = column(c);
                col.setGravity(Gravity.CENTER_HORIZONTAL);
                String when = i == 0 ? "Now"
                        : hourFormat.format(new Date((long) (h.optDouble("at") * 1000))).toLowerCase(Locale.US);
                col.addView(centred(label(c, when, 12, i == 0 ? t.accent : t.dim)));
                col.addView(centred(label(c, icon(h.optString("condition"), h.optBoolean("is_day", true)), 18, t.text)));
                col.addView(centred(label(c, h.isNull("temp_f") ? "--" : h.optInt("temp_f") + "°", 15, t.text)));
                col.addView(centred(label(c, rain + "%", 12, rain >= 30 ? t.accent : t.dim)));
                strip.addView(col, new LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1f));
            }
            box.addView(strip);
        }

        LinearLayout tiles = row(c);
        SimpleDateFormat clock = new SimpleDateFormat("h:mm a", Locale.US);
        if (!w.isNull("sunrise")) tile(c, d, t, tiles, "SUNRISE", clock.format(new Date((long) (w.optDouble("sunrise") * 1000))));
        if (!w.isNull("sunset")) tile(c, d, t, tiles, "SUNSET", clock.format(new Date((long) (w.optDouble("sunset") * 1000))));
        if (!w.isNull("wind_mph")) {
            int wind = w.optInt("wind_mph");
            int gust = w.isNull("gust_mph") ? 0 : w.optInt("gust_mph");
            tile(c, d, t, tiles, "WIND", wind + (gust >= wind + 10 ? " · " + gust : "") + " mph");
        }
        if (!w.isNull("rain_chance")) tile(c, d, t, tiles, "RAIN", w.optInt("rain_chance") + "%");
        if (tiles.getChildCount() > 0) box.addView(tiles);
    }

    private static void tile(Context c, float d, Theme t, LinearLayout tiles, String name, String value) {
        LinearLayout tile = column(c);
        tile.setPadding(px(d, 12), px(d, 8), px(d, 12), px(d, 10));
        GradientDrawable bg = new GradientDrawable();
        bg.setColor(Theme.alpha(t.panel, 200));
        bg.setCornerRadius(px(d, 10));
        tile.setBackground(bg);
        TextView label = label(c, name, 10, t.dim);
        label.setLetterSpacing(0.15f);
        tile.addView(label);
        TextView v = label(c, value, 15, t.text);
        v.setSingleLine(true);
        tile.addView(v);
        LinearLayout.LayoutParams lp = new LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1f);
        if (tiles.getChildCount() > 0) lp.setMarginStart(px(d, 8));
        tiles.addView(tile, lp);
    }

    /** brain/weather.py's condition word as the home screen's emoji. */
    static String icon(String condition, boolean day) {
        switch (condition) {
            case "clear": return day ? "☀" : "🌙";
            case "partly": return day ? "⛅" : "☁";
            case "fog": return "🌫";
            case "drizzle":
            case "rain": return "🌧";
            case "snow": return "❄";
            case "storm": return "⛈";
            default: return "☁";
        }
    }

    // --- calendar_day -------------------------------------------------------------

    private static void calendar(Context c, float d, Theme t, LinearLayout box, JSONObject data) {
        JSONArray events = data.optJSONArray("events");
        if (events == null || events.length() == 0) {
            box.addView(label(c, "Clear for the rest of today.", 18, t.dim));
            return;
        }
        SimpleDateFormat clock = new SimpleDateFormat("h:mm a", Locale.US);
        int shown = Math.min(events.length(), 7);
        for (int i = 0; i < shown; i++) {
            JSONObject e = events.optJSONObject(i);
            if (e == null) continue;
            LinearLayout line = row(c);
            line.setPadding(0, i == 0 ? 0 : px(d, 10), 0, 0);
            String when = e.optBoolean("all_day") ? "All day"
                    : clock.format(new Date((long) (e.optDouble("start_at") * 1000)));
            TextView time = label(c, when, 15, t.accent);
            line.addView(time, new LinearLayout.LayoutParams(px(d, 96), LinearLayout.LayoutParams.WRAP_CONTENT));
            LinearLayout text = column(c);
            TextView head = label(c, e.optString("title"), 17, t.text);
            head.setMaxLines(2);
            head.setEllipsize(TextUtils.TruncateAt.END);
            text.addView(head);
            String detail = join(e.optString("location"), e.optString("calendar"));
            if (!detail.isEmpty()) {
                TextView sub = label(c, detail, 13, t.dim);
                sub.setSingleLine(true);
                sub.setEllipsize(TextUtils.TruncateAt.END);
                text.addView(sub);
            }
            line.addView(text, new LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1f));
            box.addView(line);
        }
        if (events.length() > shown) {
            TextView more = label(c, "and " + (events.length() - shown) + " more", 13, t.dim);
            more.setPadding(0, px(d, 10), 0, 0);
            box.addView(more);
        }
    }

    private static String join(String a, String b) {
        if (a.isEmpty()) return b;
        if (b.isEmpty()) return a;
        return a + " · " + b;
    }

    // --- timers ---------------------------------------------------------------------

    private static void timers(Context c, float d, Theme t, LinearLayout box, JSONObject data, long sentAt) {
        JSONArray list = data.optJSONArray("timers");
        if (list == null) return;
        int n = Math.min(list.length(), 6);
        TextView[] clocks = new TextView[n];
        JSONObject[] rows = new JSONObject[n];
        for (int i = 0; i < n; i++) {
            rows[i] = list.optJSONObject(i);
            if (rows[i] == null) rows[i] = new JSONObject();
            LinearLayout line = row(c);
            line.setGravity(Gravity.CENTER_VERTICAL);
            line.setPadding(0, i == 0 ? 0 : px(d, 10), 0, 0);
            TextView name = label(c, rows[i].optString("label"), 17, t.text);
            name.setSingleLine(true);
            name.setEllipsize(TextUtils.TruncateAt.END);
            line.addView(name, new LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1f));
            clocks[i] = label(c, "", 20, rows[i].optBoolean("alarming") ? t.warning : t.text);
            clocks[i].setFontFeatureSettings("tnum");
            line.addView(clocks[i]);
            box.addView(line);
        }
        ticker = new Ticker(clocks, rows, sentAt);
        ticker.run();
    }

    /** Rewrites each countdown once a second (text only, nothing moves). */
    private static final class Ticker implements Runnable {
        private final TextView[] clocks;
        private final JSONObject[] rows;
        private final long sentAt;

        Ticker(TextView[] clocks, JSONObject[] rows, long sentAt) {
            this.clocks = clocks;
            this.rows = rows;
            this.sentAt = sentAt;
        }

        @Override
        public void run() {
            long elapsed = Math.max(0, System.currentTimeMillis() - sentAt) / 1000;
            for (int i = 0; i < clocks.length; i++) {
                JSONObject r = rows[i];
                if (r.optBoolean("alarming")) {
                    clocks[i].setText("Time's up");
                    continue;
                }
                long left = (long) Math.ceil(r.optDouble("remaining_s", 0));
                if (!r.optBoolean("paused")) left = Math.max(0, left - elapsed);
                String text = left >= 3600
                        ? String.format(Locale.US, "%d:%02d:%02d", left / 3600, left / 60 % 60, left % 60)
                        : String.format(Locale.US, "%d:%02d", left / 60, left % 60);
                clocks[i].setText(r.optBoolean("paused") ? text + " paused" : text);
            }
            if (ticker == this) MAIN.postDelayed(this, 1000);
        }
    }

    // --- helpers --------------------------------------------------------------------

    private static LinearLayout row(Context c) {
        LinearLayout l = new LinearLayout(c);
        l.setOrientation(LinearLayout.HORIZONTAL);
        return l;
    }

    private static LinearLayout column(Context c) {
        LinearLayout l = new LinearLayout(c);
        l.setOrientation(LinearLayout.VERTICAL);
        return l;
    }

    private static TextView centred(TextView t) {
        t.setGravity(Gravity.CENTER_HORIZONTAL);
        return t;
    }

    private static TextView label(Context c, String text, int sp, int color) {
        TextView t = new TextView(c);
        t.setText(text);
        t.setTextSize(sp);
        t.setTextColor(color);
        return t;
    }

    private static int px(float density, int dp) {
        return Math.round(dp * density);
    }
}
