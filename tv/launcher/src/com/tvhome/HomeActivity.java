package com.tvhome;

import android.app.Activity;
import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;
import android.content.IntentFilter;
import android.graphics.Color;
import android.graphics.Typeface;
import android.graphics.drawable.GradientDrawable;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.provider.Settings;
import android.text.SpannableString;
import android.text.TextUtils;
import android.text.format.DateFormat;
import android.text.style.ForegroundColorSpan;
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

import java.util.Calendar;
import java.util.Date;
import java.util.Locale;

/**
 * JARVIS on the TV: what the TV boots into and what Home returns to. Kept
 * calm: greeting, weather and clock; what's coming up on the calendar
 * (tagged school / personal / family; work hidden by Core); what's playing
 * on Spotify with play controls; everything else (to-dos, alerts, downloads,
 * chores) in one line of small print. Then Plex and Settings (the escape
 * hatch). YouTube and the inputs are on the remote. Data comes from Core's
 * /api/glance; the last snapshot stays up when Core is unreachable.
 */
public class HomeActivity extends Activity {
    private static final String PLEX = "com.plexapp.android";
    private static final long REFRESH_MS = 60_000;
    private static final int MAX_LINES = 6;

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

    private static final int PERSONAL = Color.rgb(184, 150, 255);

    private Theme theme;
    private View firstTile;
    private TextView greeting, weather, footer, strip;
    private LinearLayout upcoming, legend;
    private final Runnable musicTick = this::musicTick;
    private ImageView art;
    private TextView musicLabel, songTitle, songArtist;
    private MediaIcon playIcon;
    private View prevButton, nextButton;

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
        handler.post(musicTick);
        if (firstTile != null) firstTile.requestFocus();
    }

    @Override
    protected void onPause() {
        handler.removeCallbacks(poll);
        handler.removeCallbacks(musicTick);
        unregisterReceiver(updated);
        super.onPause();
    }

    // Home while already home: back to the first tile.
    @Override
    protected void onNewIntent(Intent intent) {
        super.onNewIntent(intent);
        if (firstTile != null) firstTile.requestFocus();
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
        root.setPadding(dp(48), dp(28), dp(48), dp(24));
        root.setClipChildren(false);
        root.setClipToPadding(false);
        root.setBackground(new GradientDrawable(GradientDrawable.Orientation.TL_BR,
                new int[] {theme.background, theme.deep}));

        root.addView(header());

        LinearLayout main = new LinearLayout(this);
        main.setClipChildren(false);
        LinearLayout.LayoutParams mainLp = new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, 0, 1f);
        mainLp.setMargins(0, dp(18), 0, 0);
        root.addView(main, mainLp);
        LinearLayout coming = panel(main, 1.3f);
        coming.addView(eyebrow("Coming up"));
        upcoming = new LinearLayout(this);
        upcoming.setOrientation(LinearLayout.VERTICAL);
        coming.addView(upcoming, new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, 0, 1f));
        legend = new LinearLayout(this);
        legend.setGravity(Gravity.CENTER_VERTICAL);
        coming.addView(legend);
        buildMusic(panel(main, 1f));

        // Everything else, small: to-dos, alerts, downloads, chores.
        LinearLayout status = new LinearLayout(this);
        status.setGravity(Gravity.CENTER_VERTICAL);
        strip = label("", 12, theme.dim);
        strip.setSingleLine(true);
        strip.setEllipsize(TextUtils.TruncateAt.END);
        status.addView(strip, new LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f));
        footer = label("", 10, Theme.alpha(theme.text, 90));
        footer.setLetterSpacing(0.15f);
        footer.setPadding(dp(16), 0, 0, 0);
        status.addView(footer);
        LinearLayout.LayoutParams statusLp = new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT);
        statusLp.setMargins(dp(4), dp(12), 0, dp(14));
        root.addView(status, statusLp);

        LinearLayout apps = new LinearLayout(this);
        apps.setClipChildren(false);
        root.addView(apps);
        // YouTube and the HDMI inputs have their own buttons on the remote.
        addPlexTile(apps);
        addTextTile(apps, "Settings", null, () -> startSafely(new Intent(Settings.ACTION_SETTINGS)));

        setContentView(root);
    }

    private View header() {
        LinearLayout h = new LinearLayout(this);
        h.setGravity(Gravity.CENTER_VERTICAL);

        ReactorView mark = new ReactorView(this, theme.reactor, theme.success);
        LinearLayout.LayoutParams markLp = new LinearLayout.LayoutParams(dp(104), dp(104));
        markLp.setMargins(0, 0, dp(20), 0);
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

    /** A rounded glass panel in {@code row}; returns it for content. */
    private LinearLayout panel(LinearLayout row, float weight) {
        LinearLayout p = new LinearLayout(this);
        p.setOrientation(LinearLayout.VERTICAL);
        p.setPadding(dp(20), dp(16), dp(20), dp(14));
        p.setClipChildren(false);
        GradientDrawable bg = new GradientDrawable(GradientDrawable.Orientation.TOP_BOTTOM,
                new int[] {Theme.alpha(theme.raised, 225), Theme.alpha(theme.panel, 225)});
        bg.setCornerRadius(dp(18));
        bg.setStroke(dp(1), Theme.alpha(theme.accent, 40));
        p.setBackground(bg);
        LinearLayout.LayoutParams lp = new LinearLayout.LayoutParams(0,
                ViewGroup.LayoutParams.MATCH_PARENT, weight);
        lp.setMargins(row.getChildCount() == 0 ? 0 : dp(18), 0, 0, 0);
        row.addView(p, lp);
        return p;
    }

    private TextView eyebrow(String title) {
        TextView t = label(title.toUpperCase(Locale.US), 10, theme.accent);
        t.setLetterSpacing(0.2f);
        t.setPadding(0, 0, 0, dp(8));
        return t;
    }

    // --- Music ----------------------------------------------------------------

    private void buildMusic(LinearLayout card) {
        LinearLayout body = new LinearLayout(this);
        body.setGravity(Gravity.CENTER_VERTICAL);
        body.setClipChildren(false);
        card.setGravity(Gravity.CENTER_VERTICAL);
        card.addView(body, new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT));

        art = new ImageView(this);
        art.setScaleType(ImageView.ScaleType.CENTER_CROP);
        art.setBackground(rounded(Theme.alpha(theme.accent, 30), 0, 0));
        art.setClipToOutline(true);
        body.addView(art, new LinearLayout.LayoutParams(dp(132), dp(132)));

        LinearLayout text = new LinearLayout(this);
        text.setOrientation(LinearLayout.VERTICAL);
        text.setClipChildren(false);
        LinearLayout.LayoutParams textLp = new LinearLayout.LayoutParams(0,
                ViewGroup.LayoutParams.WRAP_CONTENT, 1f);
        textLp.setMargins(dp(20), 0, 0, 0);
        body.addView(text, textLp);

        musicLabel = eyebrow("Music");
        text.addView(musicLabel);
        songTitle = label("", 19, theme.text);
        songTitle.setSingleLine(true);
        songTitle.setEllipsize(TextUtils.TruncateAt.END);
        text.addView(songTitle);
        songArtist = label("", 13, theme.dim);
        songArtist.setSingleLine(true);
        songArtist.setEllipsize(TextUtils.TruncateAt.END);
        songArtist.setPadding(0, dp(2), 0, dp(12));
        text.addView(songArtist);

        LinearLayout controls = new LinearLayout(this);
        controls.setGravity(Gravity.CENTER_VERTICAL);
        controls.setClipChildren(false);
        text.addView(controls);
        prevButton = control(controls, new MediaIcon(this, MediaIcon.PREVIOUS, theme.text), false,
                () -> Music.previous(this));
        playIcon = new MediaIcon(this, MediaIcon.PLAY, theme.deep);
        control(controls, playIcon, true, () -> Music.playPause(this));
        nextButton = control(controls, new MediaIcon(this, MediaIcon.NEXT, theme.text), false,
                () -> Music.next(this));
    }

    private View control(LinearLayout row, MediaIcon icon, boolean primary, Runnable action) {
        FrameLayout b = new FrameLayout(this);
        b.setFocusable(true);
        b.setClickable(true);
        GradientDrawable bg = new GradientDrawable();
        bg.setShape(GradientDrawable.OVAL);
        bg.setColor(primary ? theme.accent : Theme.alpha(theme.text, 26));
        b.setBackground(bg);
        b.addView(icon, new FrameLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT));
        b.setOnFocusChangeListener((v, focused) -> {
            GradientDrawable ring = new GradientDrawable();
            ring.setShape(GradientDrawable.OVAL);
            ring.setStroke(dp(2), theme.text);
            b.setForeground(focused ? ring : null);
            v.animate().scaleX(focused ? 1.12f : 1f).scaleY(focused ? 1.12f : 1f)
                    .setDuration(120).start();
        });
        b.setOnClickListener(v -> {
            action.run();
            handler.removeCallbacks(musicTick);
            handler.postDelayed(musicTick, 350);
        });
        int size = primary ? dp(48) : dp(38);
        LinearLayout.LayoutParams lp = new LinearLayout.LayoutParams(size, size);
        lp.setMargins(0, 0, dp(14), 0);
        row.addView(b, lp);
        return b;
    }

    private void musicTick() {
        Music.Now n = Music.now(this);
        if (!n.allowed) {
            musicLabel.setText("MUSIC");
            songTitle.setText("Music is switched off");
            songArtist.setText("Run tv/launcher/deploy.sh once more.");
        } else if (n.title == null) {
            musicLabel.setText("MUSIC");
            songTitle.setText("Nothing playing");
            songArtist.setText("Press play for Spotify.");
        } else {
            musicLabel.setText(n.playing ? "NOW PLAYING" : "PAUSED");
            songTitle.setText(n.title);
            songArtist.setText(n.artist != null ? n.artist : "");
        }
        art.setImageBitmap(n.art);
        playIcon.setKind(n.playing ? MediaIcon.PAUSE : MediaIcon.PLAY);
        float extras = n.title != null ? 1f : 0.35f;
        prevButton.setAlpha(extras);
        nextButton.setAlpha(extras);
        handler.postDelayed(musicTick, 1000);
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

        upcoming.removeAllViews();
        legend.removeAllViews();
        if (g == null) {
            line(upcoming, Jarvis.provisioned(this) ? "Reaching Core…" : "Not linked to Core yet.", theme.dim);
            strip.setText("");
        } else {
            JSONObject now = g.optJSONObject("now");
            if (now == null) now = new JSONObject();
            fillUpcoming(now, g.optJSONArray("events"));
            strip.setText(stripText(now, g.optJSONArray("alerts"), g.optJSONArray("downloads")));
        }
        footer.setText(statusLine());
    }

    private void fillUpcoming(JSONObject now, JSONArray events) {
        int shown = 0;
        JSONArray timers = now.optJSONArray("timers");
        for (int i = 0; timers != null && i < timers.length() && shown < MAX_LINES; i++, shown++) {
            JSONObject t = timers.optJSONObject(i);
            boolean ringing = "alarming".equals(t.optString("state"));
            row(upcoming, ringing ? "now" : minutes(t.optInt("remaining_seconds")),
                    t.optString("label", "Timer"), ringing ? theme.critical : theme.accent, 0);
        }
        java.util.LinkedHashSet<String> seen = new java.util.LinkedHashSet<>();
        for (int i = 0; events != null && i < events.length() && shown < MAX_LINES; i++, shown++) {
            JSONObject e = events.optJSONObject(i);
            String category = e.optString("category", "personal");
            seen.add(category);
            row(upcoming, when(e), e.optString("title"), theme.dim, categoryColor(category));
        }
        if (shown == 0) {
            String err = now.optString("calendar_error");
            line(upcoming, err.isEmpty() ? "Nothing on the books, sir." : err, theme.dim);
        }
        for (String category : seen) {
            legend.addView(dot(categoryColor(category)));
            TextView name = label(category.substring(0, 1).toUpperCase(Locale.US)
                    + category.substring(1), 10, theme.dim);
            name.setPadding(dp(6), 0, dp(16), 0);
            legend.addView(name);
        }
    }

    private int categoryColor(String category) {
        switch (category) {
            case "school": return theme.accent;
            case "family": return theme.success;
            case "work": return theme.warning;
            default: return PERSONAL;
        }
    }

    /** "4:30pm" today, "tomorrow", a weekday this week, else "nov 9". */
    private String when(JSONObject event) {
        long ms = (long) (event.optDouble("start_at") * 1000);
        Calendar day = Calendar.getInstance();
        day.setTimeInMillis(ms);
        Calendar today = Calendar.getInstance();
        long days = (startOfDay(day) - startOfDay(today)) / 86_400_000L;
        if (days <= 0) return event.optBoolean("all_day") ? "today" : clock(ms);
        if (days == 1) return "tomorrow";
        if (days < 7) return DateFormat.format("EEEE", ms).toString().toLowerCase(Locale.US);
        return DateFormat.format("MMM d", ms).toString().toLowerCase(Locale.US);
    }

    private static long startOfDay(Calendar c) {
        Calendar d = (Calendar) c.clone();
        d.set(Calendar.HOUR_OF_DAY, 0);
        d.set(Calendar.MINUTE, 0);
        d.set(Calendar.SECOND, 0);
        d.set(Calendar.MILLISECOND, 0);
        return d.getTimeInMillis();
    }

    /** The small print: to-dos, what needs attention, downloads, chores. */
    private CharSequence stripText(JSONObject now, JSONArray alerts, JSONArray downloads) {
        android.text.SpannableStringBuilder out = new android.text.SpannableStringBuilder();
        JSONArray reminders = now.optJSONArray("reminders");
        int todos = reminders != null ? reminders.length() : 0;
        append(out, todos == 0 ? "No to-dos" : todos == 1 ? "1 to-do" : todos + " to-dos", 0);
        for (int i = 0; alerts != null && i < alerts.length(); i++) {
            JSONObject a = alerts.optJSONObject(i);
            if ("info".equals(a.optString("severity"))) continue;
            append(out, a.optString("title"), theme.severity(a.optString("severity")));
            break;
        }
        if (downloads != null && downloads.length() > 0) {
            JSONObject d = downloads.optJSONObject(0);
            append(out, "↓ " + d.optString("name") + " " + Math.round(d.optDouble("progress") * 100) + "%"
                    + (downloads.length() > 1 ? " +" + (downloads.length() - 1) : ""), 0);
        }
        JSONArray chores = now.optJSONArray("maintenance");
        int due = 0;
        for (int i = 0; chores != null && i < chores.length(); i++) {
            if (chores.optJSONObject(i).optInt("days_remaining") <= 0) due++;
        }
        if (due > 0) append(out, due == 1 ? "1 chore due" : due + " chores due", 0);
        return out;
    }

    private void append(android.text.SpannableStringBuilder out, String text, int color) {
        if (out.length() > 0) out.append("     ·     ");
        int start = out.length();
        out.append(text);
        if (color != 0) out.setSpan(new ForegroundColorSpan(color), start, out.length(), 0);
    }

    private View dot(int color) {
        View d = new View(this);
        GradientDrawable g = new GradientDrawable();
        g.setShape(GradientDrawable.OVAL);
        g.setColor(color);
        d.setBackground(g);
        d.setLayoutParams(new LinearLayout.LayoutParams(dp(8), dp(8)));
        return d;
    }

    private void row(LinearLayout parent, String lead, String text, int leadColor, int dotColor) {
        LinearLayout r = new LinearLayout(this);
        r.setGravity(Gravity.CENTER_VERTICAL);
        r.setPadding(0, dp(4), 0, dp(4));
        TextView l = label(lead, 11, leadColor);
        l.setTypeface(Typeface.MONOSPACE);
        l.setSingleLine(true);
        r.addView(l, new LinearLayout.LayoutParams(dp(78), ViewGroup.LayoutParams.WRAP_CONTENT));
        if (dotColor != 0) {
            View d = dot(dotColor);
            LinearLayout.LayoutParams dl = new LinearLayout.LayoutParams(dp(8), dp(8));
            dl.setMargins(0, 0, dp(12), 0);
            r.addView(d, dl);
        }
        TextView t = label(text, 15, theme.text);
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

    /** Plex in JARVIS's colours: the wordmark with the chevron "x" in the accent. */
    private void addPlexTile(LinearLayout row) {
        if (getPackageManager().getLeanbackLaunchIntentForPackage(PLEX) == null
                && getPackageManager().getLaunchIntentForPackage(PLEX) == null) return;
        FrameLayout tile = tileFrame();
        GradientDrawable bg = new GradientDrawable(GradientDrawable.Orientation.TL_BR,
                new int[] {theme.raised, theme.deep});
        bg.setCornerRadius(dp(12));
        bg.setStroke(dp(1), Theme.alpha(theme.accent, 70));
        tile.setBackground(bg);

        LinearLayout inner = new LinearLayout(this);
        inner.setOrientation(LinearLayout.VERTICAL);
        inner.setGravity(Gravity.CENTER);
        SpannableString word = new SpannableString("plex");
        word.setSpan(new ForegroundColorSpan(theme.accent), 3, 4, 0);
        TextView mark = label("", 34, theme.text);
        mark.setText(word);
        mark.setTypeface(Typeface.create("sans-serif-black", Typeface.BOLD));
        mark.setShadowLayer(dp(10), 0, 0, Theme.alpha(theme.accent, 110));
        mark.setIncludeFontPadding(false);
        mark.setGravity(Gravity.CENTER);
        mark.setPadding(dp(12), 0, dp(12), 0); // room for the glow
        inner.addView(mark);
        TextView sub = label("LIBRARY", 9, Theme.alpha(theme.accent, 180));
        sub.setLetterSpacing(0.3f);
        sub.setGravity(Gravity.CENTER);
        sub.setPadding(0, dp(4), 0, 0);
        inner.addView(sub);
        tile.addView(inner, new FrameLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT));
        tile.setOnClickListener(v -> launchPackage(PLEX));
        addTile(row, tile, dp(200), dp(90));
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

    private void startSafely(Intent i) {
        try {
            i.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
            startActivity(i);
        } catch (Exception ignored) {
        }
    }
}
