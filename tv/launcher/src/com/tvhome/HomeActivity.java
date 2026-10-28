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
import android.text.SpannableString;
import android.text.TextUtils;
import android.text.format.DateFormat;
import android.text.style.ForegroundColorSpan;
import android.view.Gravity;
import android.view.KeyEvent;
import android.view.View;
import android.view.ViewGroup;
import android.widget.FrameLayout;
import android.widget.ImageView;
import android.widget.LinearLayout;
import android.widget.ScrollView;
import android.widget.TextClock;
import android.widget.TextView;

import org.json.JSONArray;
import org.json.JSONObject;

import java.util.ArrayList;
import java.util.Calendar;
import java.util.Date;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Locale;

/**
 * JARVIS on the TV: what the TV boots into and what Home returns to. Calm by
 * design: greeting, the reactor in the middle, the clock; then Next up (a few
 * calendar items, coloured by calendar), School (the next assignment),
 * Systems (all running, or what isn't, and a download in flight) and the
 * music player. Every card opens its full list (Back closes it). Bottom row:
 * Plex, Spotify, the wake-up alarm, and Settings in the corner (JARVIS's own
 * settings, with Android's behind them). YouTube and the inputs live on the
 * remote. Data comes from Core's /api/glance; the last snapshot stays up
 * when Core is unreachable.
 */
public class HomeActivity extends Activity {
    private static final String PLEX = "com.plexapp.android";
    private static final long REFRESH_MS = 60_000;
    private static final int NEXT_UP = 4;

    // Calendar colours (Nick's picks), the same in every theme.
    private static final int FAMILY = Color.rgb(255, 92, 98);
    private static final int PERSONAL = Color.rgb(255, 206, 72);
    private static final int SCHOOL = Color.rgb(184, 146, 255);
    private static final int WORK = Color.rgb(255, 150, 70);

    private final Handler handler = new Handler(Looper.getMainLooper());
    private final Runnable poll = this::pollTick;
    private final Runnable musicTick = this::musicTick;
    private final Runnable moneyTick = this::moneyTick;
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
    private FrameLayout stage;
    private LinearLayout screen;
    private View firstTile, overlay, focusBeforeOverlay;
    /** Invisible focus target for "nothing highlighted" (Back on the home screen). */
    private View rest;
    private View focusBeforeRest;
    private boolean resting;
    private TextView greeting, weather, money;
    private LinearLayout nextUp, legend, school, systems;
    private TextView systemsState, wakeTime, wakeDays, continueTitle, continueDetail;
    private View continueTile;
    private List<ContinueWatching.Item> continueItems = new ArrayList<>();
    private ImageView art;
    private TextView musicLabel, songTitle, songArtist;
    private MediaIcon playIcon;
    private View prevButton, playButton, nextButton;
    private ReactorView reactor;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        theme = Jarvis.theme(this);
        build();
        RemoteBridgeService.start(this);
    }

    @Override
    protected void onResume() {
        super.onResume();
        if (Jarvis.theme(this) != theme) {
            recreate();
            return;
        }
        registerReceiver(updated, new IntentFilter(Jarvis.ACTION_UPDATED));
        render();
        handler.post(poll);
        handler.post(musicTick);
        handler.post(moneyTick);
        resting = false;
        if (overlay == null && firstTile != null) firstTile.requestFocus();
    }

    @Override
    protected void onPause() {
        handler.removeCallbacks(poll);
        handler.removeCallbacks(musicTick);
        handler.removeCallbacks(moneyTick);
        Beat.stop();
        try {
            unregisterReceiver(updated);
        } catch (IllegalArgumentException ignored) {
            // onResume recreated before registering
        }
        super.onPause();
    }

    // Home while already home: close any detail and back to the first tile.
    @Override
    protected void onNewIntent(Intent intent) {
        super.onNewIntent(intent);
        closeOverlay();
        resting = false;
        if (firstTile != null) firstTile.requestFocus();
    }

    // Back closes a detail; otherwise it hides the highlight, so nothing is lit.
    @Override
    public void onBackPressed() {
        if (overlay != null) {
            closeOverlay();
        } else if (!resting) {
            focusBeforeRest = getCurrentFocus();
            resting = true;
            rest.requestFocus();
        }
    }

    /** While resting, the first arrow/OK only brings the highlight back. */
    @Override
    public boolean dispatchKeyEvent(KeyEvent event) {
        if (resting && overlay == null) {
            switch (event.getKeyCode()) {
                case KeyEvent.KEYCODE_DPAD_UP:
                case KeyEvent.KEYCODE_DPAD_DOWN:
                case KeyEvent.KEYCODE_DPAD_LEFT:
                case KeyEvent.KEYCODE_DPAD_RIGHT:
                case KeyEvent.KEYCODE_DPAD_CENTER:
                case KeyEvent.KEYCODE_ENTER:
                    if (event.getAction() == KeyEvent.ACTION_UP) {
                        resting = false;
                        View back = focusBeforeRest != null ? focusBeforeRest : firstTile;
                        if (back != null) back.requestFocus();
                    }
                    return true;
                default:
                    break;
            }
        }
        return super.dispatchKeyEvent(event);
    }

    /**
     * Every 5 s while home is showing: the shared theme (/api/theme) and
     * Schwab, live (/api/portfolio; Core streams the prices) rather than the
     * whole glance. The portfolio part idles when Schwab isn't linked (the
     * glance has no portfolio).
     */
    private void moneyTick() {
        // The shared theme, too: a pick on the phone lands here within ~5 s.
        new Thread(() -> {
            if (Jarvis.followTheme(this)) runOnUiThread(this::recreate);
        }, "theme").start();
        JSONObject g = Jarvis.cached(this);
        if (g != null && g.optJSONObject("portfolio") != null) {
            new Thread(() -> {
                JSONObject p = Jarvis.portfolio(this);
                if (p != null) runOnUiThread(() -> showMoney(p.has("value") ? p : null));
            }, "portfolio").start();
        }
        handler.postDelayed(moneyTick, 5000);
    }

    private void showMoney(JSONObject p) {
        if (p == null || !p.has("value")) {
            money.setVisibility(View.GONE);
            return;
        }
        double change = p.optDouble("today_change", 0);
        String text = String.format(Locale.US, "Portfolio $%,.2f   %s $%,.2f (%+.2f%%) today",
                p.optDouble("value"), change >= 0 ? "▲" : "▼", Math.abs(change), p.optDouble("today_pct", 0));
        android.text.SpannableString line = new android.text.SpannableString(text);
        int start = text.indexOf(change >= 0 ? "▲" : "▼");
        line.setSpan(new android.text.style.ForegroundColorSpan(change >= 0 ? theme.success : theme.critical),
                start, text.length() - " today".length(), 0);
        money.setText(line);
        money.setVisibility(View.VISIBLE);
    }

    private void pollTick() {
        fetch();
        new Thread(() -> {
            List<ContinueWatching.Item> items = ContinueWatching.fetch();
            runOnUiThread(() -> showContinue(items));
        }, "continue").start();
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
        stage = new FrameLayout(this);
        stage.setBackground(new GradientDrawable(GradientDrawable.Orientation.TL_BR,
                new int[] {theme.background, theme.deep}));
        screen = new LinearLayout(this);
        screen.setOrientation(LinearLayout.VERTICAL);
        screen.setPadding(dp(48), dp(22), dp(48), dp(26));
        screen.setClipChildren(false);
        screen.setClipToPadding(false);
        stage.addView(screen);
        rest = new View(this);
        rest.setFocusable(true);
        stage.addView(rest, new FrameLayout.LayoutParams(1, 1));

        screen.addView(header());

        LinearLayout cards = new LinearLayout(this);
        cards.setClipChildren(false);
        LinearLayout.LayoutParams cardsLp = new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, 0, 1f);
        cardsLp.setMargins(0, dp(14), 0, 0);
        screen.addView(cards, cardsLp);

        LinearLayout next = card(cards, 1.25f, true, this::showAgenda);
        next.addView(eyebrow("Next up", theme.accent));
        nextUp = new LinearLayout(this);
        nextUp.setOrientation(LinearLayout.VERTICAL);
        next.addView(nextUp, new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, 0, 1f));
        legend = new LinearLayout(this);
        legend.setGravity(Gravity.CENTER_VERTICAL);
        next.addView(legend);

        LinearLayout middle = new LinearLayout(this);
        middle.setOrientation(LinearLayout.VERTICAL);
        middle.setClipChildren(false);
        LinearLayout.LayoutParams middleLp = new LinearLayout.LayoutParams(0,
                ViewGroup.LayoutParams.MATCH_PARENT, 0.95f);
        middleLp.setMargins(dp(16), 0, 0, 0);
        cards.addView(middle, middleLp);
        LinearLayout schoolCard = card(middle, 1f, true, this::showSchool);
        schoolCard.addView(eyebrow("School", SCHOOL));
        school = new LinearLayout(this);
        school.setOrientation(LinearLayout.VERTICAL);
        schoolCard.addView(school);
        LinearLayout systemsCard = card(middle, 1f, true, this::showSystems);
        LinearLayout systemsHead = new LinearLayout(this);
        systemsHead.addView(eyebrow("Systems", theme.accent),
                new LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f));
        systemsState = label("", 9, theme.dim);
        systemsState.setLetterSpacing(0.15f);
        systemsHead.addView(systemsState);
        systemsCard.addView(systemsHead);
        systems = new LinearLayout(this);
        systems.setOrientation(LinearLayout.VERTICAL);
        systemsCard.addView(systems);

        LinearLayout musicCard = card(cards, 1.15f, false, null);
        buildMusic(musicCard);
        // Right from School or Systems goes to the player, not down to the
        // nearest bottom tile; left from the player comes back to School.
        playButton.setId(View.generateViewId());
        schoolCard.setId(View.generateViewId());
        schoolCard.setNextFocusRightId(playButton.getId());
        systemsCard.setNextFocusRightId(playButton.getId());
        prevButton.setNextFocusLeftId(schoolCard.getId());

        LinearLayout apps = new LinearLayout(this);
        apps.setClipChildren(false);
        apps.setGravity(Gravity.BOTTOM);
        LinearLayout.LayoutParams appsLp = new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT);
        appsLp.setMargins(-dp(8), dp(16), -dp(8), 0); // tiles carry 8dp each side
        screen.addView(apps, appsLp);
        // YouTube and the HDMI inputs have their own buttons on the remote.
        addPlexTile(apps);
        addSpotifyTile(apps);
        addWakeTile(apps);
        addContinueTile(apps);
        addSettingsTile(apps);

        setContentView(stage);
    }

    /** Greeting on the left, the reactor dead centre, the clock on the right. */
    private View header() {
        FrameLayout h = new FrameLayout(this);
        h.setClipChildren(false); // the reactor bounces past its box to Back in Black

        LinearLayout words = new LinearLayout(this);
        words.setOrientation(LinearLayout.VERTICAL);
        greeting = label("", 26, theme.text);
        greeting.setTypeface(Typeface.create("sans-serif-light", Typeface.NORMAL));
        words.addView(greeting);
        weather = label("", 13, theme.dim);
        weather.setPadding(0, dp(2), 0, 0);
        words.addView(weather);
        money = label("", 13, theme.dim);
        money.setPadding(0, dp(2), 0, 0);
        words.addView(money);
        h.addView(words, new FrameLayout.LayoutParams(ViewGroup.LayoutParams.WRAP_CONTENT,
                ViewGroup.LayoutParams.WRAP_CONTENT, Gravity.START | Gravity.CENTER_VERTICAL));

        reactor = new ReactorView(this, theme.reactor, theme.success);
        h.addView(reactor, new FrameLayout.LayoutParams(dp(124), dp(124), Gravity.CENTER));

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
        String dateFormat = (theme == Theme.MCQUEEN ? "'⚡ '" : "") + "EEEE, MMMM d";
        date.setFormat12Hour(dateFormat);
        date.setFormat24Hour(dateFormat);
        date.setTextSize(12);
        date.setTextColor(theme.accent);
        date.setLetterSpacing(0.08f);
        time.addView(date);
        h.addView(time, new FrameLayout.LayoutParams(ViewGroup.LayoutParams.WRAP_CONTENT,
                ViewGroup.LayoutParams.WRAP_CONTENT, Gravity.END | Gravity.CENTER_VERTICAL));
        return h;
    }

    /** A glass card in {@code parent} (a row or a column); focusable if it opens something. */
    private LinearLayout card(LinearLayout parent, float weight, boolean focusable, Runnable open) {
        LinearLayout c = new LinearLayout(this);
        c.setOrientation(LinearLayout.VERTICAL);
        c.setPadding(dp(20), dp(14), dp(20), dp(12));
        c.setClipChildren(false);
        GradientDrawable bg = new GradientDrawable(GradientDrawable.Orientation.TOP_BOTTOM,
                new int[] {Theme.alpha(theme.raised, 225), Theme.alpha(theme.panel, 225)});
        bg.setCornerRadius(dp(18));
        bg.setStroke(dp(1), Theme.alpha(theme.accent, 40));
        c.setBackground(bg);
        if (focusable) {
            c.setFocusable(true);
            c.setClickable(true);
            c.setOnClickListener(v -> open.run());
            c.setOnFocusChangeListener((v, focused) -> {
                GradientDrawable ring = new GradientDrawable();
                ring.setCornerRadius(dp(18));
                ring.setStroke(dp(2), theme.accent);
                v.setForeground(focused ? ring : null);
                v.animate().scaleX(focused ? 1.025f : 1f).scaleY(focused ? 1.025f : 1f)
                        .translationZ(focused ? dp(6) : 0).setDuration(140).start();
            });
        }
        boolean column = parent.getOrientation() == LinearLayout.VERTICAL;
        LinearLayout.LayoutParams lp = column
                ? new LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, 0, weight)
                : new LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.MATCH_PARENT, weight);
        if (parent.getChildCount() > 0) {
            if (column) lp.setMargins(0, dp(14), 0, 0);
            else lp.setMargins(dp(16), 0, 0, 0);
        }
        parent.addView(c, lp);
        return c;
    }

    private TextView eyebrow(String title, int color) {
        TextView t = label(title.toUpperCase(Locale.US), 10, color);
        t.setLetterSpacing(0.2f);
        t.setPadding(0, 0, 0, dp(6));
        return t;
    }

    // --- Music ----------------------------------------------------------------

    private void buildMusic(LinearLayout card) {
        card.setGravity(Gravity.CENTER_VERTICAL);
        card.setPadding(dp(18), dp(14), dp(18), dp(14));
        LinearLayout body = new LinearLayout(this);
        body.setGravity(Gravity.CENTER_VERTICAL);
        body.setClipChildren(false);
        card.addView(body, new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT));

        art = new ImageView(this);
        art.setScaleType(ImageView.ScaleType.CENTER_CROP);
        art.setBackground(rounded(Theme.alpha(theme.accent, 30), 0, 0));
        art.setClipToOutline(true);
        body.addView(art, new LinearLayout.LayoutParams(dp(100), dp(100)));

        LinearLayout text = new LinearLayout(this);
        text.setOrientation(LinearLayout.VERTICAL);
        text.setClipChildren(false);
        LinearLayout.LayoutParams textLp = new LinearLayout.LayoutParams(0,
                ViewGroup.LayoutParams.WRAP_CONTENT, 1f);
        textLp.setMargins(dp(16), 0, 0, 0);
        body.addView(text, textLp);

        musicLabel = eyebrow("Music", theme.accent);
        text.addView(musicLabel);
        songTitle = label("", 18, theme.text);
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
        playButton = control(controls, playIcon, true, () -> Music.playPause(this));
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
        int size = primary ? dp(44) : dp(34);
        LinearLayout.LayoutParams lp = new LinearLayout.LayoutParams(size, size);
        lp.setMargins(0, 0, dp(10), 0);
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
        // Easter egg: the reactor dances to Back in Black.
        boolean acdc = n.playing && n.title != null
                && n.title.toLowerCase(Locale.US).startsWith("back in black");
        if (acdc) Beat.start(this, reactor);
        else Beat.stop();
        handler.removeCallbacks(musicTick);
        handler.postDelayed(musicTick, 1000);
    }

    // --- Content ------------------------------------------------------------

    private void render() {
        JSONObject g = Jarvis.cached(this);
        if (Jarvis.theme(this) != theme) {
            recreate();
            return;
        }
        String address = g != null ? g.optString("address", "sir") : "sir";
        // A little ka-chow on the McQueen theme.
        greeting.setText(theme == Theme.MCQUEEN ? "Ka-chow, " + address + "."
                : greetingText() + ", " + address + ".");
        weather.setText(g != null ? weatherLine(g.optJSONObject("weather")) : "");
        showMoney(g != null ? g.optJSONObject("portfolio") : null);
        systemsState.setText(statusLine());

        nextUp.removeAllViews();
        legend.removeAllViews();
        school.removeAllViews();
        systems.removeAllViews();
        if (g == null) {
            line(nextUp, Jarvis.provisioned(this) ? "Reaching Core…" : "Not linked to Core yet.", theme.dim, 14);
        } else {
            JSONObject now = g.optJSONObject("now");
            if (now == null) now = new JSONObject();
            fillNextUp(now, g.optJSONArray("events"));
            fillSchool(now.optJSONArray("academic_events"));
            fillSystems(g, now);
        }
        fillWake(g != null ? g.optJSONObject("wake") : null);
    }

    private void fillNextUp(JSONObject now, JSONArray events) {
        int shown = 0;
        JSONArray timers = now.optJSONArray("timers");
        for (int i = 0; timers != null && i < timers.length() && shown < NEXT_UP; i++, shown++) {
            JSONObject t = timers.optJSONObject(i);
            boolean ringing = "alarming".equals(t.optString("state"));
            row(nextUp, ringing ? "now" : minutes(t.optInt("remaining_seconds")),
                    t.optString("label", "Timer"), ringing ? theme.critical : theme.accent, 0, 15);
        }
        LinkedHashSet<String> seen = new LinkedHashSet<>();
        for (int i = 0; events != null && i < events.length() && shown < NEXT_UP; i++) {
            JSONObject e = events.optJSONObject(i);
            String category = e.optString("category", "personal");
            if ("school".equals(category)) continue; // School has its own card
            seen.add(category);
            row(nextUp, when(e), e.optString("title"), theme.dim, categoryColor(category), 15);
            shown++;
        }
        if (shown == 0) {
            String err = now.optString("calendar_error");
            line(nextUp, err.isEmpty() ? "Nothing on the books, sir." : err, theme.dim, 14);
        }
        for (String category : seen) {
            legend.addView(dot(categoryColor(category)));
            TextView name = label(title(category), 10, theme.dim);
            name.setPadding(dp(6), 0, dp(16), 0);
            legend.addView(name);
        }
    }

    private void fillSchool(JSONArray academic) {
        JSONObject first = academic != null && academic.length() > 0 ? academic.optJSONObject(0) : null;
        if (first == null) {
            line(school, "Nothing due. Enjoy it.", theme.dim, 14);
            return;
        }
        TextView t = label(first.optString("title"), 16, theme.text);
        t.setSingleLine(true);
        t.setEllipsize(TextUtils.TruncateAt.END);
        school.addView(t);
        int days = first.optInt("days_left");
        String in = days <= 0 ? "today" : days == 1 ? "tomorrow" : "in " + days + " days";
        TextView w = label(in + "  ·  " + day(first.optDouble("start_at")), 12, SCHOOL);
        w.setPadding(0, dp(2), 0, 0);
        school.addView(w);
        int more = 0;
        for (int i = 1; i < academic.length(); i++) {
            if (academic.optJSONObject(i).optInt("days_left") <= 14) more++;
        }
        if (more > 0) {
            TextView m = label(more + " more in the next two weeks", 11, theme.dim);
            m.setPadding(0, dp(4), 0, 0);
            school.addView(m);
        }
    }

    private void fillSystems(JSONObject g, JSONObject now) {
        JSONObject worst = worstAlert(g.optJSONArray("alerts"));
        if (worst == null) {
            line(systems, "All systems running.", theme.success, 15);
        } else {
            TextView t = label(worst.optString("title"), 15, theme.severity(worst.optString("severity")));
            t.setSingleLine(true);
            t.setEllipsize(TextUtils.TruncateAt.END);
            systems.addView(t);
        }
        JSONArray downloads = g.optJSONArray("downloads");
        if (Jarvis.flag(this, Jarvis.DOWNLOADS) && downloads != null && downloads.length() > 0) {
            JSONObject d = downloads.optJSONObject(0);
            line(systems, "↓ " + Math.round(d.optDouble("progress") * 100) + "%  " + d.optString("name")
                    + (downloads.length() > 1 ? "  +" + (downloads.length() - 1) : ""), theme.dim, 12);
        }
        StringBuilder small = new StringBuilder();
        JSONArray reminders = now.optJSONArray("reminders");
        int todos = reminders != null ? reminders.length() : 0;
        small.append(todos == 0 ? "No to-dos" : todos == 1 ? "1 to-do" : todos + " to-dos");
        int chores = choresDue(now);
        if (chores > 0) small.append("  ·  ").append(chores == 1 ? "1 chore due" : chores + " chores due");
        line(systems, small.toString(), theme.dim, 11);
    }

    private void fillWake(JSONObject wake) {
        if (wake == null) {
            setWordmark(wakeTime, "not set", -1);
            wakeDays.setText("Set it in the app");
        } else {
            setWordmark(wakeTime, wakeTime(wake.optString("time")), -1);
            String label = wake.optString("label");
            wakeDays.setText(label.isEmpty() ? wake.optString("days") : wake.optString("days") + " · " + label);
        }
    }

    // --- Details (every card opens one; Back closes it) ------------------------

    private void showAgenda() {
        JSONObject g = Jarvis.cached(this);
        JSONArray events = g != null ? g.optJSONArray("events") : null;
        List<View> rows = new ArrayList<>();
        for (int i = 0; events != null && i < events.length(); i++) {
            JSONObject e = events.optJSONObject(i);
            rows.add(rowView(when(e), e.optString("title"), theme.dim,
                    categoryColor(e.optString("category", "personal")), 16));
        }
        if (rows.isEmpty()) rows.add(lineView("Nothing in the next two weeks, sir.", theme.dim, 15));
        openOverlay("Next two weeks", rows);
    }

    private void showSchool() {
        JSONObject g = Jarvis.cached(this);
        JSONObject now = g != null ? g.optJSONObject("now") : null;
        JSONArray academic = now != null ? now.optJSONArray("academic_events") : null;
        List<View> rows = new ArrayList<>();
        for (int i = 0; academic != null && i < academic.length(); i++) {
            JSONObject a = academic.optJSONObject(i);
            int days = a.optInt("days_left");
            rows.add(rowView(days <= 0 ? "today" : days == 1 ? "tomorrow" : days + " days",
                    a.optString("title") + "   " + day(a.optDouble("start_at")),
                    days <= 2 ? theme.warning : SCHOOL, SCHOOL, 16));
        }
        if (rows.isEmpty()) rows.add(lineView("Nothing due. Enjoy it.", theme.dim, 15));
        openOverlay("School", rows);
    }

    private void showSystems() {
        JSONObject g = Jarvis.cached(this);
        List<View> rows = new ArrayList<>();
        if (g == null) {
            rows.add(lineView("No word from Core yet.", theme.dim, 15));
            openOverlay("Systems", rows);
            return;
        }
        JSONArray alerts = g.optJSONArray("alerts");
        rows.add(sectionView("Alerts (last day)"));
        int count = 0;
        for (int i = 0; alerts != null && i < alerts.length(); i++) {
            JSONObject a = alerts.optJSONObject(i);
            if ("wake_up".equals(a.optString("kind"))) continue;
            rows.add(rowView(clock((long) (a.optDouble("created_at") * 1000)), a.optString("title"),
                    theme.severity(a.optString("severity")), 0, 15));
            count++;
        }
        if (count == 0) rows.add(lineView("None. All systems running.", theme.success, 15));
        JSONArray downloads = g.optJSONArray("downloads");
        if (downloads != null && downloads.length() > 0) {
            rows.add(sectionView("Downloading"));
            for (int i = 0; i < downloads.length(); i++) {
                JSONObject d = downloads.optJSONObject(i);
                rows.add(rowView(Math.round(d.optDouble("progress") * 100) + "%", d.optString("name"),
                        theme.accent, 0, 15));
            }
        }
        JSONObject now = g.optJSONObject("now");
        JSONArray reminders = now != null ? now.optJSONArray("reminders") : null;
        rows.add(sectionView("To-dos"));
        for (int i = 0; reminders != null && i < reminders.length(); i++) {
            JSONObject r = reminders.optJSONObject(i);
            boolean due = r.optBoolean("is_due");
            rows.add(rowView(due ? "due" : "·", r.optString("text"), due ? theme.warning : theme.dim, 0, 15));
        }
        if (reminders == null || reminders.length() == 0) rows.add(lineView("All clear.", theme.dim, 15));
        JSONArray chores = now != null ? now.optJSONArray("maintenance") : null;
        if (chores != null && chores.length() > 0) {
            rows.add(sectionView("Around the house"));
            for (int i = 0; i < chores.length(); i++) {
                JSONObject m = chores.optJSONObject(i);
                int days = m.optInt("days_remaining");
                rows.add(rowView(days < 0 ? "overdue" : days == 0 ? "due" : days + " days",
                        m.optString("name"), days <= 0 ? theme.warning : theme.dim, 0, 15));
            }
        }
        openOverlay("Systems", rows);
    }

    private void openOverlay(String title, List<View> rows) {
        closeOverlay();
        focusBeforeOverlay = getCurrentFocus();
        FrameLayout dim = new FrameLayout(this);
        dim.setBackgroundColor(Theme.alpha(theme.deep, 215));
        dim.setClickable(true);

        LinearLayout panel = new LinearLayout(this);
        panel.setOrientation(LinearLayout.VERTICAL);
        panel.setPadding(dp(28), dp(22), dp(28), dp(18));
        GradientDrawable bg = new GradientDrawable(GradientDrawable.Orientation.TOP_BOTTOM,
                new int[] {theme.raised, theme.panel});
        bg.setCornerRadius(dp(20));
        bg.setStroke(dp(1), Theme.alpha(theme.accent, 70));
        panel.setBackground(bg);
        panel.addView(eyebrow(title, theme.accent));

        ScrollView scroll = new ScrollView(this);
        scroll.setFocusable(true); // D-pad scrolls long lists
        LinearLayout list = new LinearLayout(this);
        list.setOrientation(LinearLayout.VERTICAL);
        for (View r : rows) list.addView(r);
        scroll.addView(list);
        panel.addView(scroll, new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT, 1f));
        TextView hint = label("BACK TO CLOSE", 9, theme.dim);
        hint.setLetterSpacing(0.2f);
        hint.setGravity(Gravity.END);
        hint.setPadding(0, dp(10), 0, 0);
        panel.addView(hint);

        FrameLayout.LayoutParams lp = new FrameLayout.LayoutParams(dp(600),
                ViewGroup.LayoutParams.WRAP_CONTENT, Gravity.CENTER);
        lp.setMargins(0, dp(40), 0, dp(40));
        dim.addView(panel, lp);
        screen.setDescendantFocusability(ViewGroup.FOCUS_BLOCK_DESCENDANTS);
        stage.addView(dim);
        overlay = dim;
        scroll.requestFocus();
    }

    private void closeOverlay() {
        if (overlay == null) return;
        stage.removeView(overlay);
        overlay = null;
        screen.setDescendantFocusability(ViewGroup.FOCUS_AFTER_DESCENDANTS);
        if (focusBeforeOverlay != null) focusBeforeOverlay.requestFocus();
    }

    // --- Rows and helpers -----------------------------------------------------

    private void row(LinearLayout parent, String lead, String text, int leadColor, int dotColor, int sp) {
        parent.addView(rowView(lead, text, leadColor, dotColor, sp));
    }

    private View rowView(String lead, String text, int leadColor, int dotColor, int sp) {
        LinearLayout r = new LinearLayout(this);
        r.setGravity(Gravity.CENTER_VERTICAL);
        r.setPadding(0, dp(4), 0, dp(4));
        TextView l = label(lead, 11, leadColor);
        l.setTypeface(Typeface.MONOSPACE);
        l.setSingleLine(true);
        r.addView(l, new LinearLayout.LayoutParams(dp(78), ViewGroup.LayoutParams.WRAP_CONTENT));
        if (dotColor != 0) {
            LinearLayout.LayoutParams dl = new LinearLayout.LayoutParams(dp(8), dp(8));
            dl.setMargins(0, 0, dp(12), 0);
            r.addView(dot(dotColor), dl);
        }
        TextView t = label(text, sp, theme.text);
        t.setSingleLine(true);
        t.setEllipsize(TextUtils.TruncateAt.END);
        r.addView(t, new LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f));
        return r;
    }

    private void line(LinearLayout parent, String text, int color, int sp) {
        parent.addView(lineView(text, color, sp));
    }

    private View lineView(String text, int color, int sp) {
        TextView t = label(text, sp, color);
        t.setSingleLine(true);
        t.setEllipsize(TextUtils.TruncateAt.END);
        t.setPadding(0, dp(3), 0, dp(3));
        return t;
    }

    private View sectionView(String title) {
        TextView t = label(title.toUpperCase(Locale.US), 9, theme.accent);
        t.setLetterSpacing(0.2f);
        t.setPadding(0, dp(12), 0, dp(4));
        return t;
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

    private static int categoryColor(String category) {
        switch (category) {
            case "school": return SCHOOL;
            case "family": return FAMILY;
            case "work": return WORK;
            default: return PERSONAL;
        }
    }

    private static String title(String word) {
        return word.isEmpty() ? word : word.substring(0, 1).toUpperCase(Locale.US) + word.substring(1);
    }

    private static JSONObject worstAlert(JSONArray alerts) {
        JSONObject worst = null;
        for (int i = 0; alerts != null && i < alerts.length(); i++) {
            JSONObject a = alerts.optJSONObject(i);
            String s = a.optString("severity");
            if ("critical".equals(s)) return a;
            if ("warning".equals(s) && worst == null) worst = a;
        }
        return worst;
    }

    private static int choresDue(JSONObject now) {
        JSONArray chores = now.optJSONArray("maintenance");
        int due = 0;
        for (int i = 0; chores != null && i < chores.length(); i++) {
            if (chores.optJSONObject(i).optInt("days_remaining") <= 0) due++;
        }
        return due;
    }

    /** "4:30pm" today, "tomorrow", a weekday this week, else "nov 9". */
    private String when(JSONObject event) {
        long ms = (long) (event.optDouble("start_at") * 1000);
        Calendar day = Calendar.getInstance();
        day.setTimeInMillis(ms);
        long days = (startOfDay(day) - startOfDay(Calendar.getInstance())) / 86_400_000L;
        if (days <= 0) return event.optBoolean("all_day") ? "today" : clock(ms);
        if (days == 1) return "tomorrow";
        if (days < 7) return DateFormat.format("EEEE", ms).toString().toLowerCase(Locale.US);
        return DateFormat.format("MMM d", ms).toString().toLowerCase(Locale.US);
    }

    private static String day(double startAt) {
        return startAt > 0 ? DateFormat.format("EEEE", (long) (startAt * 1000)).toString() : "";
    }

    private static long startOfDay(Calendar c) {
        Calendar d = (Calendar) c.clone();
        d.set(Calendar.HOUR_OF_DAY, 0);
        d.set(Calendar.MINUTE, 0);
        d.set(Calendar.SECOND, 0);
        d.set(Calendar.MILLISECOND, 0);
        return d.getTimeInMillis();
    }

    /** "07:00" -> "7:00am". */
    static String wakeTime(String hhmm) {
        try {
            int h = Integer.parseInt(hhmm.substring(0, 2));
            String m = hhmm.substring(3, 5);
            return (h % 12 == 0 ? 12 : h % 12) + ":" + m + (h < 12 ? "am" : "pm");
        } catch (RuntimeException e) {
            return hhmm;
        }
    }

    private String statusLine() {
        if (!Jarvis.provisioned(this)) return "NOT LINKED";
        long ok = Jarvis.fetchedAt(this), failed = Jarvis.failedAt(this);
        if (ok == 0) return failed > 0 ? "CORE UNREACHABLE" : "CONNECTING";
        if (failed > ok) return "CORE UNREACHABLE · LAST SEEN " + clock(ok);
        return "CORE ONLINE";
    }

    private static String weatherLine(JSONObject w) {
        if (w == null || !w.optBoolean("online")) return "";
        StringBuilder s = new StringBuilder();
        String desc = w.optString("description");
        s.append(weatherIcon(desc)).append("  ").append(w.optInt("temperature_f")).append("°  ");
        if (!desc.isEmpty()) s.append(Character.toUpperCase(desc.charAt(0))).append(desc.substring(1));
        if (!w.isNull("high_f") && !w.isNull("low_f")) {
            s.append("  ·  H ").append(w.optInt("high_f")).append("  L ").append(w.optInt("low_f"));
        }
        return s.toString();
    }

    /** One small icon for the sky (brain/weather.py's descriptions); a moon on clear nights. */
    static String weatherIcon(String desc) {
        String d = desc.toLowerCase(Locale.US);
        int hour = java.util.Calendar.getInstance().get(java.util.Calendar.HOUR_OF_DAY);
        boolean night = hour >= 19 || hour < 6;
        if (d.contains("thunder")) return "⛈";
        if (d.contains("snow")) return "❄";
        if (d.contains("rain") || d.contains("drizzle") || d.contains("shower")) return "🌧";
        if (d.contains("fog")) return "🌫";
        if (d.contains("partly") || d.contains("mostly clear")) return night ? "☁" : "⛅";
        if (d.contains("overcast") || d.contains("cloud")) return "☁";
        if (d.contains("clear")) return night ? "🌙" : "☀";
        return "";
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

    // --- Bottom row -------------------------------------------------------------

    /** Plex in JARVIS's colours: the wordmark with the chevron "x" in the accent. */
    private void addPlexTile(LinearLayout row) {
        if (getPackageManager().getLeanbackLaunchIntentForPackage(PLEX) == null
                && getPackageManager().getLaunchIntentForPackage(PLEX) == null) return;
        wordmarkTile(row, "plex", 3, "LIBRARY", () -> launchPackage(PLEX));
    }

    /** Spotify, same treatment: the "i" is the accent. */
    private void addSpotifyTile(LinearLayout row) {
        if (getPackageManager().getLeanbackLaunchIntentForPackage(Music.SPOTIFY) == null) return;
        wordmarkTile(row, "spotify", 4, "MUSIC", () -> Music.openSpotify(this));
    }

    private void wordmarkTile(LinearLayout row, String word, int accentAt, String sub, Runnable open) {
        TextView mark = wordmark();
        setWordmark(mark, word, accentAt);
        wordTile(row, mark, sub, null, open);
    }

    /**
     * Every bottom-row tile is built the same way, so the wordmarks sit on one
     * line and the labels on another: a bold wordmark (one accent letter), its
     * spaced label, and small print that only shows while the tile is
     * highlighted (the wordmark and label slide up to make room).
     */
    private FrameLayout wordTile(LinearLayout row, TextView mark, String sub, TextView detail, Runnable open) {
        FrameLayout tile = tileFrame();
        LinearLayout inner = new LinearLayout(this);
        inner.setOrientation(LinearLayout.VERTICAL);
        inner.setGravity(Gravity.CENTER);
        inner.addView(mark, new LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, dp(38)));
        inner.addView(tileLabel(sub));
        if (detail == null) detail = tileInfo();
        detail.setVisibility(View.GONE); // centred until highlighted
        inner.addView(detail);
        inner.setLayoutTransition(new android.animation.LayoutTransition()); // slide, don't jump
        tile.setTag(detail);
        centreText(inner);
        tile.addView(inner, new FrameLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT));
        tile.setOnClickListener(v -> open.run());
        addTile(row, tile);
        return tile;
    }

    /** Bold like the Plex and Spotify wordmarks; shrinks to fit long titles. */
    private TextView wordmark() {
        TextView t = label("", 30, theme.text);
        t.setTypeface(Typeface.create("sans-serif-black", Typeface.BOLD));
        t.setShadowLayer(dp(10), 0, 0, Theme.alpha(theme.accent, 110));
        t.setIncludeFontPadding(false);
        t.setGravity(Gravity.CENTER);
        t.setPadding(dp(10), 0, dp(10), 0);
        t.setMaxLines(1);
        t.setEllipsize(TextUtils.TruncateAt.END);
        t.setAutoSizeTextTypeUniformWithConfiguration(14, 30, 1, android.util.TypedValue.COMPLEX_UNIT_SP);
        return t;
    }

    /**
     * Lower-case, one character in the accent: {@code accentAt}, or (-1) one
     * picked from the text itself, so it stays put while the text does.
     */
    private void setWordmark(TextView t, String text, int accentAt) {
        String word = text.toLowerCase(Locale.US);
        SpannableString span = new SpannableString(word);
        int at = accentAt;
        if (at < 0) {
            List<Integer> letters = new ArrayList<>();
            for (int i = 0; i < word.length(); i++) if (Character.isLetterOrDigit(word.charAt(i))) letters.add(i);
            at = letters.isEmpty() ? -1 : letters.get(Math.abs(word.hashCode() % letters.size()));
        }
        if (at >= 0 && at < word.length()) span.setSpan(new ForegroundColorSpan(theme.accent), at, at + 1, 0);
        t.setText(span);
    }

    /** The wake-up alarm at a glance; set by asking JARVIS on the phone. */
    private void addWakeTile(LinearLayout row) {
        wakeTime = wordmark();
        wakeDays = tileInfo();
        wordTile(row, wakeTime, "WAKE-UP", wakeDays, this::showWake);
    }

    /** Plex's Continue Watching: the newest one on the tile, the rest a press away. */
    private void addContinueTile(LinearLayout row) {
        continueTitle = wordmark();
        continueDetail = tileInfo();
        continueTile = wordTile(row, continueTitle, "CONTINUE", continueDetail, this::showContinueList);
        continueTile.setVisibility(View.GONE); // until Plex answers with something
    }

    private void showContinue(List<ContinueWatching.Item> items) {
        if (continueTile == null || isFinishing()) return;
        continueItems = items;
        if (items.isEmpty()) {
            if (continueTile.hasFocus() && firstTile != null) firstTile.requestFocus();
            continueTile.setVisibility(View.GONE);
            return;
        }
        setWordmark(continueTitle, items.get(0).title, -1);
        continueDetail.setText(items.get(0).detail);
        continueTile.setVisibility(View.VISIBLE);
    }

    private void showContinueList() {
        if (continueItems.size() == 1) {
            ContinueWatching.open(this, continueItems.get(0));
            return;
        }
        List<View> rows = new ArrayList<>();
        for (ContinueWatching.Item item : continueItems) {
            LinearLayout r = new LinearLayout(this);
            r.setOrientation(LinearLayout.VERTICAL);
            r.setPadding(dp(14), dp(8), dp(14), dp(8));
            r.setFocusable(true);
            r.setClickable(true);
            r.addView(label(item.title, 16, theme.text));
            r.addView(label(item.detail, 11, theme.dim));
            r.setOnFocusChangeListener((v, focused) ->
                    v.setBackground(focused ? rounded(Theme.alpha(theme.accent, 40), 0, 0) : null));
            r.setOnClickListener(v -> {
                closeOverlay();
                ContinueWatching.open(this, item);
            });
            rows.add(r);
        }
        openOverlay("Continue watching", rows);
        rows.get(0).requestFocus(); // the newest is one OK away
    }

    private void showWake() {
        JSONObject g = Jarvis.cached(this);
        JSONObject wake = g != null ? g.optJSONObject("wake") : null;
        List<View> rows = new ArrayList<>();
        if (wake == null) {
            rows.add(lineView("No wake-up alarm yet.", theme.text, 16));
        } else {
            String label = wake.optString("label");
            rows.add(lineView("Music on at " + wakeTime(wake.optString("time")) + ", " + wake.optString("days")
                    + (label.isEmpty() ? "." : ": " + label + "."), theme.text, 16));
        }
        rows.add(sectionView("Change it"));
        rows.add(lineView("Each day's time: Settings > Wake-up in the phone app.", theme.dim, 13));
        rows.add(lineView("Just one day: tell JARVIS \"skip my alarm tomorrow\" or \"8:30 tomorrow\".", theme.dim, 13));
        openOverlay("Wake-up", rows);
    }

    /** Small, in the far corner: JARVIS's settings (Android's are behind them). */
    private void addSettingsTile(LinearLayout row) {
        TextView mark = wordmark();
        setWordmark(mark, "settings", -1);
        wordTile(row, mark, "JARVIS", null, () -> startSafely(new Intent(this, SettingsActivity.class)));
    }

    /** The wordmarks' spaced label (LIBRARY, MUSIC, WAKE-UP). */
    private TextView tileLabel(String text) {
        TextView t = label(text, 9, Theme.alpha(theme.accent, 180));
        t.setLetterSpacing(0.3f);
        t.setPadding(0, dp(4), 0, 0);
        return t;
    }

    private TextView tileInfo() {
        TextView t = label("", 10, theme.dim);
        t.setSingleLine(true);
        t.setEllipsize(TextUtils.TruncateAt.END);
        t.setPadding(0, dp(3), 0, 0);
        return t;
    }

    /** Bottom-row text sits centred, like the Plex and Spotify wordmarks. */
    private static void centreText(LinearLayout inner) {
        for (int i = 0; i < inner.getChildCount(); i++) {
            if (inner.getChildAt(i) instanceof TextView) ((TextView) inner.getChildAt(i)).setGravity(Gravity.CENTER);
        }
    }

    private FrameLayout tileFrame() {
        FrameLayout tile = new FrameLayout(this);
        tile.setFocusable(true);
        tile.setClickable(true);
        // Every bottom-row tile looks like the Plex and Spotify ones.
        GradientDrawable bg = new GradientDrawable(GradientDrawable.Orientation.TL_BR,
                new int[] {theme.raised, theme.deep});
        bg.setCornerRadius(dp(12));
        bg.setStroke(dp(1), Theme.alpha(theme.accent, 70));
        tile.setBackground(bg);
        tile.setClipToOutline(true);
        tile.setOnFocusChangeListener((v, focused) -> {
            tile.setForeground(focused ? rounded(Color.TRANSPARENT, dp(3), theme.accent) : null);
            // A wordTile's small print (its tag) only shows while it's highlighted.
            if (v.getTag() instanceof TextView) {
                TextView detail = (TextView) v.getTag();
                detail.setVisibility(focused && detail.length() > 0 ? View.VISIBLE : View.GONE);
            }
            v.animate().scaleX(focused ? 1.08f : 1f).scaleY(focused ? 1.08f : 1f)
                    .translationZ(focused ? dp(8) : 0).setDuration(120).start();
        });
        return tile;
    }

    /** Bottom row: every tile the same width, 16dp apart, spread edge to edge. */
    private void addTile(LinearLayout row, View tile) {
        LinearLayout.LayoutParams lp = new LinearLayout.LayoutParams(0, dp(84), 1f);
        lp.setMargins(dp(8), 0, dp(8), 0);
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
