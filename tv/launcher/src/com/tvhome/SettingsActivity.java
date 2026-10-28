package com.tvhome;

import android.app.Activity;
import android.content.Intent;
import android.graphics.Typeface;
import android.graphics.drawable.GradientDrawable;
import android.os.Bundle;
import android.provider.Settings;
import android.view.Gravity;
import android.view.View;
import android.view.ViewGroup;
import android.widget.LinearLayout;
import android.widget.ScrollView;
import android.widget.TextView;

import org.json.JSONObject;

/**
 * JARVIS's own settings, in front of Android's: the choices made for Nick
 * that he may want to change. Each row is one press to change; Android
 * settings is the last row. Stored on the TV (Jarvis.prefs).
 */
public class SettingsActivity extends Activity {
    private static final String[] THEMES = {"arc", "markIII", "hotRod"};
    private static final String[] THEME_NAMES = {"Arc", "Mark III", "Hot Rod"};

    private Theme theme;
    private LinearLayout list;
    private int focusIndex;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        build();
    }

    private void build() {
        theme = Jarvis.theme(this);
        LinearLayout root = new LinearLayout(this);
        root.setOrientation(LinearLayout.VERTICAL);
        root.setPadding(dp(96), dp(40), dp(96), dp(32));
        root.setBackground(new GradientDrawable(GradientDrawable.Orientation.TL_BR,
                new int[] {theme.background, theme.deep}));

        LinearLayout head = new LinearLayout(this);
        head.setGravity(Gravity.CENTER_VERTICAL);
        head.addView(new ReactorView(this, theme.reactor, theme.success),
                new LinearLayout.LayoutParams(dp(56), dp(56)));
        LinearLayout words = new LinearLayout(this);
        words.setOrientation(LinearLayout.VERTICAL);
        words.setPadding(dp(18), 0, 0, 0);
        TextView title = label("Settings", 26, theme.text);
        title.setTypeface(Typeface.create("sans-serif-light", Typeface.NORMAL));
        words.addView(title);
        words.addView(label("What I've set up for you, sir. Press to change.", 13, theme.dim));
        head.addView(words);
        root.addView(head);

        ScrollView scroll = new ScrollView(this);
        list = new LinearLayout(this);
        list.setOrientation(LinearLayout.VERTICAL);
        list.setPadding(0, dp(20), 0, dp(20));
        list.setClipChildren(false);
        scroll.setClipChildren(false);
        scroll.addView(list);
        root.addView(scroll, new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, 0, 1f));

        String current = Jarvis.prefs(this).getString(Jarvis.THEME, "");
        int t = 0;
        for (int i = 0; i < THEMES.length; i++) if (THEMES[i].equals(current)) t = i;
        if (current.isEmpty()) t = indexOf(themeFromCore());
        final int next = (t + 1) % THEMES.length;
        row("Colour theme", THEME_NAMES[t], "Arc, Mark III or Hot Rod, like the phone.", () -> {
            Jarvis.prefs(this).edit().putString(Jarvis.THEME, THEMES[next]).apply();
            rebuild(0);
        });
        toggle(1, "Read replies aloud", Jarvis.SPEAK,
                "When you ask \"Jarvis, …\" on the remote. Better once his own voice is in.");
        toggle(2, "Show downloads", Jarvis.DOWNLOADS, "Downloads in the Systems card.");
        toggle(3, "Nightly sound refresh", Jarvis.REFRESH,
                "Restarts the TV quietly after you switch it off, so sound never goes missing.");
        row("Wake-up music", wakeText(), "Set or change it by asking JARVIS on your phone.", null);
        row("Android settings", "", "Wi-Fi, picture, sound, apps.",
                () -> startActivity(new Intent(Settings.ACTION_SETTINGS)));

        setContentView(root);
        View focus = list.getChildAt(Math.min(focusIndex, list.getChildCount() - 1));
        if (focus != null) focus.requestFocus();
    }

    private void rebuild(int focus) {
        focusIndex = focus;
        build();
    }

    private void toggle(int index, String name, String key, String hint) {
        boolean on = Jarvis.flag(this, key);
        row(name, on ? "On" : "Off", hint, () -> {
            Jarvis.prefs(this).edit().putBoolean(key, !on).apply();
            rebuild(index);
        });
    }

    private void row(String name, String value, String hint, Runnable action) {
        LinearLayout r = new LinearLayout(this);
        r.setGravity(Gravity.CENTER_VERTICAL);
        r.setPadding(dp(22), dp(14), dp(22), dp(14));
        r.setFocusable(true);
        r.setClickable(action != null);
        GradientDrawable bg = new GradientDrawable();
        bg.setColor(Theme.alpha(theme.raised, 200));
        bg.setCornerRadius(dp(14));
        r.setBackground(bg);
        r.setOnFocusChangeListener((v, focused) -> {
            GradientDrawable ring = new GradientDrawable();
            ring.setCornerRadius(dp(14));
            ring.setStroke(dp(2), theme.accent);
            v.setForeground(focused ? ring : null);
            v.animate().scaleX(focused ? 1.02f : 1f).scaleY(focused ? 1.02f : 1f).setDuration(120).start();
        });
        if (action != null) r.setOnClickListener(v -> action.run());

        LinearLayout text = new LinearLayout(this);
        text.setOrientation(LinearLayout.VERTICAL);
        text.addView(label(name, 17, theme.text));
        text.addView(label(hint, 12, theme.dim));
        r.addView(text, new LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f));
        TextView v = label(value, 15, theme.accent);
        v.setTypeface(Typeface.create("sans-serif-medium", Typeface.NORMAL));
        r.addView(v);

        LinearLayout.LayoutParams lp = new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT);
        lp.setMargins(0, 0, 0, dp(10));
        list.addView(r, lp);
    }

    private String themeFromCore() {
        JSONObject g = Jarvis.cached(this);
        return g != null ? g.optString("theme") : "arc";
    }

    private static int indexOf(String name) {
        for (int i = 0; i < THEMES.length; i++) if (THEMES[i].equals(name)) return i;
        return 0;
    }

    private String wakeText() {
        JSONObject g = Jarvis.cached(this);
        JSONObject w = g != null ? g.optJSONObject("wake") : null;
        if (w == null) return "Not set";
        return HomeActivity.wakeTime(w.optString("time")) + " · " + w.optString("days");
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
}
