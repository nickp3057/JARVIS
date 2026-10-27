package com.tvhome;

import android.app.Activity;
import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;
import android.content.IntentFilter;
import android.content.pm.ApplicationInfo;
import android.content.pm.PackageManager;
import android.graphics.Color;
import android.graphics.Typeface;
import android.graphics.drawable.Drawable;
import android.graphics.drawable.GradientDrawable;
import android.media.tv.TvContract;
import android.media.tv.TvInputInfo;
import android.media.tv.TvInputManager;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.provider.Settings;
import android.text.TextUtils;
import android.text.format.DateFormat;
import android.view.Gravity;
import android.view.View;
import android.view.ViewGroup;
import android.widget.FrameLayout;
import android.widget.ImageView;
import android.widget.LinearLayout;
import android.widget.TextClock;
import android.widget.TextView;

import org.json.JSONArray;
import org.json.JSONObject;

import java.util.ArrayList;
import java.util.Calendar;
import java.util.Comparator;
import java.util.Date;
import java.util.List;
import java.util.Locale;

/**
 * JARVIS on the TV: what the TV boots into and what Home returns to. A glance
 * from the couch (greeting, weather, today, to-dos, what needs attention)
 * from Core's /api/glance, in the phone's theme, over a row of the only apps
 * that matter: Plex, YouTube, the HDMI inputs and Settings (the escape hatch
 * to stock Android). Shows the last snapshot when Core is unreachable.
 */
public class HomeActivity extends Activity {
    private static final String PLEX = "com.plexapp.android";
    private static final String YOUTUBE = "com.google.android.youtube.tv";
    private static final long REFRESH_MS = 60_000;
    private static final int MAX_LINES = 5;

    private final Handler handler = new Handler(Looper.getMainLooper());
    private final Runnable poll = this::pollTick;
    private final BroadcastReceiver updated = new GlanceReceiver(this);

    /**
     * Re-renders when the service or a fetch stores a new snapshot. Static:
     * d8 34 can't read the constructor javac 24 writes for an inner class.
     */
    private static final class GlanceReceiver extends BroadcastReceiver {
        private final HomeActivity home;

        GlanceReceiver(HomeActivity home) {
            this.home = home;
        }

        @Override
        public void onReceive(Context context, Intent intent) {
            home.render();
        }
    }

    private Theme theme;
    private View firstTile;
    private TextView greeting, weather, footer;
    private LinearLayout today, todos, attention;
    private TextView todosTitle;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        JSONObject glance = Jarvis.cached(this);
        theme = Theme.named(glance != null ? glance.optString("theme") : "");
        build();
        RemoteBridgeService.start(this);
    }

    @Override
    protected void onResume() {
        super.onResume();
        registerReceiver(updated, new IntentFilter(Jarvis.ACTION_UPDATED));
        render();
        handler.post(poll);
        if (firstTile != null) firstTile.requestFocus();
    }

    @Override
    protected void onPause() {
        handler.removeCallbacks(poll);
        unregisterReceiver(updated);
        super.onPause();
    }

    // Back on the home screen does nothing; it is the bottom of the stack.
    @Override
    public void onBackPressed() {}

    private void pollTick() {
        fetch();
        handler.postDelayed(poll, REFRESH_MS);
    }

    private void fetch() {
        new Thread(() -> {
            try {
                Jarvis.refresh(this);
            } catch (Exception ignored) {
                // render() shows the cached snapshot and the offline note.
            }
        }, "glance").start();
    }

    // --- Layout -------------------------------------------------------------

    private void build() {
        LinearLayout root = new LinearLayout(this);
        root.setOrientation(LinearLayout.VERTICAL);
        root.setPadding(dp(48), dp(30), dp(48), dp(22));
        root.setClipChildren(false);
        root.setClipToPadding(false);
        root.setBackground(new GradientDrawable(GradientDrawable.Orientation.TL_BR,
                new int[] {theme.background, theme.deep}));

        root.addView(header());

        LinearLayout panels = new LinearLayout(this);
        LinearLayout.LayoutParams panelsLp = new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, 0, 1f);
        panelsLp.setMargins(0, dp(22), 0, dp(4));
        root.addView(panels, panelsLp);
        today = panel(panels, "Today", null);
        todosTitle = new TextView(this);
        todos = panel(panels, "To-dos", todosTitle);
        attention = panel(panels, "Needs you", null);

        root.addView(sectionLabel("Watch"));
        LinearLayout apps = new LinearLayout(this);
        apps.setClipChildren(false);
        root.addView(apps);
        addAppTile(apps, PLEX);
        addAppTile(apps, YOUTUBE);
        addInputTiles(apps);
        addTextTile(apps, "Settings", null, () -> startSafely(new Intent(Settings.ACTION_SETTINGS)));

        footer = label("", 10, theme.dim);
        footer.setLetterSpacing(0.15f);
        footer.setGravity(Gravity.END);
        footer.setPadding(0, dp(14), 0, 0);
        root.addView(footer, new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT));

        setContentView(root);
    }

    private View header() {
        LinearLayout h = new LinearLayout(this);
        h.setGravity(Gravity.CENTER_VERTICAL);

        ReactorView mark = new ReactorView(this, theme.reactor);
        LinearLayout.LayoutParams markLp = new LinearLayout.LayoutParams(dp(64), dp(64));
        markLp.setMargins(0, 0, dp(18), 0);
        h.addView(mark, markLp);

        LinearLayout words = new LinearLayout(this);
        words.setOrientation(LinearLayout.VERTICAL);
        greeting = label("", 26, theme.text);
        greeting.setTypeface(Typeface.create("sans-serif-light", Typeface.NORMAL));
        words.addView(greeting);
        weather = label("", 13, theme.dim);
        weather.setPadding(0, dp(2), 0, 0);
        words.addView(weather);
        h.addView(words, new LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f));

        LinearLayout time = new LinearLayout(this);
        time.setOrientation(LinearLayout.VERTICAL);
        time.setGravity(Gravity.END);
        TextClock clock = new TextClock(this);
        clock.setFormat12Hour("h:mm");
        clock.setFormat24Hour("H:mm");
        clock.setTextSize(40);
        clock.setTextColor(theme.text);
        clock.setTypeface(Typeface.create("sans-serif-thin", Typeface.NORMAL));
        time.addView(clock);
        TextClock date = new TextClock(this);
        date.setFormat12Hour("EEEE, MMMM d");
        date.setFormat24Hour("EEEE, MMMM d");
        date.setTextSize(12);
        date.setTextColor(theme.accent);
        date.setLetterSpacing(0.08f);
        time.addView(date);
        h.addView(time);
        return h;
    }

    private LinearLayout panel(LinearLayout row, String title, TextView titleView) {
        LinearLayout p = new LinearLayout(this);
        p.setOrientation(LinearLayout.VERTICAL);
        p.setPadding(dp(18), dp(14), dp(18), dp(14));
        GradientDrawable bg = new GradientDrawable(GradientDrawable.Orientation.TOP_BOTTOM,
                new int[] {Theme.alpha(theme.raised, 235), Theme.alpha(theme.panel, 235)});
        bg.setCornerRadius(dp(16));
        bg.setStroke(dp(1), Theme.alpha(theme.accent, 46));
        p.setBackground(bg);

        TextView t = titleView != null ? titleView : new TextView(this);
        t.setText(title.toUpperCase(Locale.US));
        t.setTextSize(10);
        t.setTextColor(theme.accent);
        t.setLetterSpacing(0.2f);
        t.setPadding(0, 0, 0, dp(8));
        p.addView(t);

        LinearLayout body = new LinearLayout(this);
        body.setOrientation(LinearLayout.VERTICAL);
        p.addView(body);

        LinearLayout.LayoutParams lp = new LinearLayout.LayoutParams(0,
                ViewGroup.LayoutParams.MATCH_PARENT, 1f);
        lp.setMargins(row.getChildCount() == 0 ? 0 : dp(16), 0, 0, 0);
        row.addView(p, lp);
        return body;
    }

    private TextView sectionLabel(String title) {
        TextView t = label(title.toUpperCase(Locale.US), 10, theme.dim);
        t.setLetterSpacing(0.2f);
        t.setPadding(dp(2), dp(16), 0, dp(8));
        return t;
    }

    // --- Content ------------------------------------------------------------

    private void render() {
        JSONObject g = Jarvis.cached(this);
        if (g != null && Theme.named(g.optString("theme")) != theme) {
            recreate();
            return;
        }
        String address = g != null ? g.optString("address", "sir") : "sir";
        greeting.setText(greetingText() + ", " + address + ".");
        weather.setText(g != null ? weatherLine(g.optJSONObject("weather")) : "");

        today.removeAllViews();
        todos.removeAllViews();
        attention.removeAllViews();
        if (g == null) {
            String note = Jarvis.provisioned(this) ? "Reaching Core…" : "Not linked to Core yet.";
            line(today, note, theme.dim);
            todosTitle.setText("TO-DOS");
        } else {
            JSONObject now = g.optJSONObject("now");
            fillToday(now != null ? now : new JSONObject());
            fillTodos(now != null ? now.optJSONArray("reminders") : null);
            fillAttention(g.optJSONArray("alerts"), g.optJSONArray("downloads"));
        }
        footer.setText(statusLine());
    }

    private void fillToday(JSONObject now) {
        int shown = 0;
        JSONArray timers = now.optJSONArray("timers");
        for (int i = 0; timers != null && i < timers.length() && shown < MAX_LINES; i++, shown++) {
            JSONObject t = timers.optJSONObject(i);
            String state = t.optString("state");
            String when = "alarming".equals(state) ? "now" : minutes(t.optInt("remaining_seconds"));
            row(today, when, t.optString("label", "Timer"),
                    "alarming".equals(state) ? theme.critical : theme.accent);
        }
        JSONObject next = now.optJSONObject("next_event");
        if (next != null && shown < MAX_LINES) {
            row(today, eventTime(next), next.optString("title"), theme.accent);
            shown++;
        }
        JSONArray academic = now.optJSONArray("academic_events");
        for (int i = 0; academic != null && i < academic.length() && shown < MAX_LINES; i++) {
            JSONObject a = academic.optJSONObject(i);
            int days = a.optInt("days_left");
            if (days > 14) continue;
            row(today, days <= 0 ? "today" : days == 1 ? "1 day" : days + " days",
                    a.optString("title"), days <= 2 ? theme.warning : theme.accent);
            shown++;
        }
        JSONArray chores = now.optJSONArray("maintenance");
        for (int i = 0; chores != null && i < chores.length() && shown < MAX_LINES; i++, shown++) {
            JSONObject m = chores.optJSONObject(i);
            int days = m.optInt("days_remaining");
            row(today, days < 0 ? "overdue" : days == 0 ? "due" : days + " days",
                    m.optString("name"), days <= 0 ? theme.warning : theme.dim);
        }
        if (shown == 0) {
            String err = now.optString("calendar_error");
            line(today, err.isEmpty() ? "Nothing on the books, sir." : err, theme.dim);
        }
    }

    private void fillTodos(JSONArray reminders) {
        int count = reminders != null ? reminders.length() : 0;
        todosTitle.setText(count > 0 ? "TO-DOS · " + count : "TO-DOS");
        for (int i = 0; i < Math.min(count, MAX_LINES + 1); i++) {
            JSONObject r = reminders.optJSONObject(i);
            if (i == MAX_LINES) {
                line(todos, "+ " + (count - MAX_LINES) + " more on your phone", theme.dim);
                break;
            }
            boolean due = r.optBoolean("is_due");
            row(todos, due ? "due" : "·", r.optString("text"), due ? theme.warning : theme.dim);
        }
        if (count == 0) line(todos, "All clear.", theme.dim);
    }

    private void fillAttention(JSONArray alerts, JSONArray downloads) {
        int shown = 0;
        for (int i = 0; alerts != null && i < alerts.length() && shown < 3; i++) {
            JSONObject a = alerts.optJSONObject(i);
            if ("info".equals(a.optString("severity"))) continue;
            row(attention, clock((long) (a.optDouble("created_at") * 1000)),
                    a.optString("title"), theme.severity(a.optString("severity")));
            shown++;
        }
        for (int i = 0; downloads != null && i < downloads.length() && shown < MAX_LINES; i++, shown++) {
            JSONObject d = downloads.optJSONObject(i);
            row(attention, Math.round(d.optDouble("progress") * 100) + "%",
                    d.optString("name"), theme.accent);
        }
        if (shown == 0) line(attention, "All systems nominal.", theme.success);
    }

    private void row(LinearLayout parent, String lead, String text, int leadColor) {
        LinearLayout r = new LinearLayout(this);
        r.setGravity(Gravity.CENTER_VERTICAL);
        r.setPadding(0, dp(3), 0, dp(3));
        TextView l = label(lead, 11, leadColor);
        l.setTypeface(Typeface.MONOSPACE);
        l.setSingleLine(true);
        r.addView(l, new LinearLayout.LayoutParams(dp(62), ViewGroup.LayoutParams.WRAP_CONTENT));
        TextView t = label(text, 14, theme.text);
        t.setSingleLine(true);
        t.setEllipsize(TextUtils.TruncateAt.END);
        r.addView(t, new LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f));
        parent.addView(r);
    }

    private void line(LinearLayout parent, String text, int color) {
        TextView t = label(text, 14, color);
        t.setPadding(0, dp(3), 0, dp(3));
        parent.addView(t);
    }

    private String statusLine() {
        if (!Jarvis.provisioned(this)) return "NOT LINKED TO CORE · RUN TV/PROVISION.SH";
        long ok = Jarvis.fetchedAt(this), failed = Jarvis.failedAt(this);
        if (ok == 0) return failed > 0 ? "CORE UNREACHABLE" : "CONNECTING TO CORE";
        if (failed > ok) return "CORE UNREACHABLE · LAST SEEN " + clock(ok);
        return "CORE ONLINE · " + clock(ok);
    }

    private static String weatherLine(JSONObject w) {
        if (w == null || !w.optBoolean("online")) return "";
        StringBuilder s = new StringBuilder();
        s.append(w.optInt("temperature_f")).append("°  ");
        String desc = w.optString("description");
        if (!desc.isEmpty()) s.append(Character.toUpperCase(desc.charAt(0))).append(desc.substring(1));
        if (!w.isNull("high_f") && !w.isNull("low_f")) {
            s.append("  ·  H ").append(w.optInt("high_f")).append("  L ").append(w.optInt("low_f"));
        }
        return s.toString();
    }

    private String eventTime(JSONObject event) {
        if (event.optBoolean("all_day")) return "all day";
        double start = event.optDouble("start_at", 0);
        if (start <= 0) return "";
        long ms = (long) (start * 1000);
        Calendar when = Calendar.getInstance();
        when.setTimeInMillis(ms);
        Calendar now = Calendar.getInstance();
        if (when.get(Calendar.YEAR) == now.get(Calendar.YEAR)
                && when.get(Calendar.DAY_OF_YEAR) == now.get(Calendar.DAY_OF_YEAR)) {
            return clock(ms);
        }
        return DateFormat.format("EEE", ms).toString().toLowerCase(Locale.US);
    }

    private String clock(long ms) {
        return DateFormat.getTimeFormat(this).format(new Date(ms)).toLowerCase(Locale.US)
                .replace(" ", "");
    }

    private static String minutes(int seconds) {
        return seconds < 60 ? seconds + "s" : (seconds + 59) / 60 + " min";
    }

    private static String greetingText() {
        int hour = Calendar.getInstance().get(Calendar.HOUR_OF_DAY);
        if (hour < 5) return "Up late";
        if (hour < 12) return "Good morning";
        if (hour < 17) return "Good afternoon";
        return "Good evening";
    }

    // --- App row --------------------------------------------------------------

    private void addInputTiles(LinearLayout row) {
        TvInputManager tim = getSystemService(TvInputManager.class);
        if (tim == null) return;
        List<TvInputInfo> list = new ArrayList<>(tim.getTvInputList());
        list.sort(Comparator.comparing(i -> i.loadLabel(this).toString()));
        for (TvInputInfo info : list) {
            if (info.getType() != TvInputInfo.TYPE_HDMI) continue;
            CharSequence custom = info.loadCustomLabel(this);
            CharSequence label = custom != null ? custom : info.loadLabel(this);
            CharSequence sub = custom != null ? info.loadLabel(this) : null;
            final String id = info.getId();
            addTextTile(row, label.toString(), sub, () -> launchInput(id));
        }
    }

    private void addAppTile(LinearLayout row, String pkg) {
        PackageManager pm = getPackageManager();
        if (pm.getLeanbackLaunchIntentForPackage(pkg) == null
                && pm.getLaunchIntentForPackage(pkg) == null) return;
        Drawable art;
        try {
            ApplicationInfo ai = pm.getApplicationInfo(pkg, 0);
            art = ai.loadBanner(pm);
            if (art == null) art = ai.loadIcon(pm);
        } catch (PackageManager.NameNotFoundException e) {
            return;
        }
        ImageView img = new ImageView(this);
        img.setImageDrawable(art);
        img.setScaleType(ImageView.ScaleType.FIT_CENTER);
        FrameLayout tile = tileFrame();
        tile.addView(img, new FrameLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT));
        tile.setOnClickListener(v -> launchPackage(pkg));
        addTile(row, tile, dp(156), dp(90));
    }

    private void addTextTile(LinearLayout row, String text, CharSequence sub, Runnable action) {
        FrameLayout tile = tileFrame();
        LinearLayout inner = new LinearLayout(this);
        inner.setOrientation(LinearLayout.VERTICAL);
        inner.setGravity(Gravity.CENTER);
        TextView main = label(text, 15, theme.text);
        main.setGravity(Gravity.CENTER);
        main.setTypeface(Typeface.create("sans-serif-medium", Typeface.NORMAL));
        inner.addView(main);
        if (sub != null) {
            TextView s = label(sub.toString(), 10, theme.dim);
            s.setGravity(Gravity.CENTER);
            s.setPadding(0, dp(3), 0, 0);
            inner.addView(s);
        }
        tile.addView(inner, new FrameLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT));
        tile.setOnClickListener(v -> action.run());
        addTile(row, tile, dp(108), dp(90));
    }

    private FrameLayout tileFrame() {
        FrameLayout tile = new FrameLayout(this);
        tile.setFocusable(true);
        tile.setClickable(true);
        tile.setBackground(rounded(theme.raised, 0, 0));
        tile.setClipToOutline(true);
        tile.setOnFocusChangeListener((v, focused) -> {
            tile.setForeground(focused ? rounded(Color.TRANSPARENT, dp(3), theme.accent) : null);
            v.animate().scaleX(focused ? 1.08f : 1f).scaleY(focused ? 1.08f : 1f)
                    .translationZ(focused ? dp(8) : 0).setDuration(120).start();
        });
        return tile;
    }

    private void addTile(LinearLayout row, View tile, int w, int h) {
        LinearLayout.LayoutParams lp = new LinearLayout.LayoutParams(w, h);
        lp.setMargins(0, 0, dp(14), 0);
        row.addView(tile, lp);
        if (firstTile == null) firstTile = tile;
    }

    private GradientDrawable rounded(int color, int stroke, int strokeColor) {
        GradientDrawable d = new GradientDrawable();
        d.setColor(color);
        d.setCornerRadius(dp(12));
        if (stroke > 0) d.setStroke(stroke, strokeColor);
        return d;
    }

    private TextView label(String text, int sp, int color) {
        TextView t = new TextView(this);
        t.setText(text);
        t.setTextSize(sp);
        t.setTextColor(color);
        return t;
    }

    private int dp(int v) {
        return Math.round(v * getResources().getDisplayMetrics().density);
    }

    private void launchPackage(String pkg) {
        Intent i = getPackageManager().getLeanbackLaunchIntentForPackage(pkg);
        if (i == null) i = getPackageManager().getLaunchIntentForPackage(pkg);
        if (i != null) startSafely(i);
    }

    private void launchInput(String inputId) {
        startSafely(new Intent(Intent.ACTION_VIEW,
                TvContract.buildChannelUriForPassthroughInput(inputId)));
    }

    private void startSafely(Intent i) {
        try {
            i.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
            startActivity(i);
        } catch (Exception ignored) {
        }
    }
}
