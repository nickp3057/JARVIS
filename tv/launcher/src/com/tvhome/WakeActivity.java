package com.tvhome;

import android.app.Activity;
import android.content.Context;
import android.graphics.Typeface;
import android.graphics.drawable.GradientDrawable;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.view.Gravity;
import android.view.KeyEvent;
import android.view.View;
import android.widget.LinearLayout;
import android.widget.TextView;

import java.text.SimpleDateFormat;
import java.util.Date;
import java.util.Locale;

/**
 * The wake-up's own screen (Wake.run starts it once the music is playing):
 * the time, "Good morning, sir." and two buttons. I'm up closes it and the
 * music plays on; Snooze pauses it and Wake runs again in 9 minutes. Back is
 * I'm up; play/pause on the remote still works.
 */
public class WakeActivity extends Activity {
    /** The one on screen, so I'm up on the phone can close it (main thread only). */
    private static WakeActivity showing;
    private final Handler handler = new Handler(Looper.getMainLooper());
    private Theme theme;
    private TextView clock, status;
    private View upButton;

    /** A method reference, not an inner class: d8 34 can't read javac 24's inner-class constructors. */
    private final Runnable tick = this::tick;

    private void tick() {
        clock.setText(new SimpleDateFormat("h:mm", Locale.getDefault()).format(new Date()));
        long left = Wake.snoozeUntil - System.currentTimeMillis();
        String label = label();
        if (left > 0) {
            status.setText("Snoozing. Back at it in " + (left / 60_000 + 1) + " min.");
        } else {
            status.setText(label.isEmpty() ? "Music's on." : label + " is on.");
        }
        handler.postDelayed(tick, 1000);
    }

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        theme = Jarvis.theme(this);
        LinearLayout root = new LinearLayout(this);
        root.setOrientation(LinearLayout.VERTICAL);
        root.setGravity(Gravity.CENTER);
        root.setBackground(new GradientDrawable(GradientDrawable.Orientation.TL_BR,
                new int[] {theme.background, theme.deep}));

        root.addView(new ReactorView(this, theme.reactor, theme.success),
                new LinearLayout.LayoutParams(dp(120), dp(120)));
        clock = label("", 96, theme.text);
        clock.setTypeface(Typeface.create("sans-serif-thin", Typeface.NORMAL));
        root.addView(clock);
        root.addView(label("Good morning, sir.", 28, theme.text));
        status = label("", 15, theme.dim);
        status.setPadding(0, dp(6), 0, dp(28));
        root.addView(status);

        LinearLayout buttons = new LinearLayout(this);
        buttons.setGravity(Gravity.CENTER);
        upButton = button(buttons, "I'm up", true, v -> awake());
        button(buttons, "Snooze 9 min", false, v -> {
            Wake.snooze(this, getIntent().getIntExtra("volume", 30), label());
            upButton.requestFocus();
        });
        root.addView(buttons);
        TextView hint = label("Play/pause on the remote stops the music.", 12, theme.dim);
        hint.setPadding(0, dp(22), 0, 0);
        root.addView(hint);
        setContentView(root);
        upButton.requestFocus();
        showing = this;
    }

    @Override
    protected void onDestroy() {
        if (showing == this) showing = null;
        super.onDestroy();
    }

    static void closeShowing() {
        if (showing != null) showing.finish();
    }

    @Override
    protected void onNewIntent(android.content.Intent intent) {
        super.onNewIntent(intent); // the snooze ended: same screen, music back on
        setIntent(intent);
        upButton.requestFocus();
    }

    @Override
    protected void onResume() {
        super.onResume();
        handler.post(tick);
    }

    @Override
    protected void onPause() {
        handler.removeCallbacks(tick);
        super.onPause();
    }

    @Override
    public void onBackPressed() {
        awake();
    }

    @Override
    public boolean onKeyDown(int keyCode, KeyEvent event) {
        if (keyCode == KeyEvent.KEYCODE_MEDIA_PLAY_PAUSE || keyCode == KeyEvent.KEYCODE_MEDIA_PLAY
                || keyCode == KeyEvent.KEYCODE_MEDIA_PAUSE) {
            Music.playPause(this);
            return true;
        }
        return super.onKeyDown(keyCode, event);
    }

    private void awake() {
        Wake.cancelSnooze();
        Context app = getApplicationContext();
        new Thread(() -> Jarvis.awake(app)).start(); // good-morning lights
        finish();
    }

    private String label() {
        String label = getIntent().getStringExtra("label");
        return label != null ? label : "";
    }

    private View button(LinearLayout row, String text, boolean primary, View.OnClickListener click) {
        TextView b = label(text, 18, primary ? theme.deep : theme.text);
        b.setTypeface(Typeface.create("sans-serif-medium", Typeface.NORMAL));
        b.setPadding(dp(34), dp(14), dp(34), dp(14));
        GradientDrawable bg = new GradientDrawable();
        bg.setColor(primary ? theme.accent : Theme.alpha(theme.text, 26));
        bg.setCornerRadius(dp(28));
        b.setBackground(bg);
        b.setFocusable(true);
        b.setOnClickListener(click);
        b.setOnFocusChangeListener((v, focused) -> {
            GradientDrawable ring = new GradientDrawable();
            ring.setCornerRadius(dp(28));
            ring.setStroke(dp(2), theme.text);
            v.setForeground(focused ? ring : null);
            v.animate().scaleX(focused ? 1.06f : 1f).scaleY(focused ? 1.06f : 1f).setDuration(120).start();
        });
        LinearLayout.LayoutParams lp = new LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.WRAP_CONTENT, LinearLayout.LayoutParams.WRAP_CONTENT);
        lp.setMargins(dp(12), 0, dp(12), 0);
        row.addView(b, lp);
        return b;
    }

    private TextView label(String text, int sp, int color) {
        TextView t = new TextView(this);
        t.setText(text);
        t.setTextSize(sp);
        t.setTextColor(color);
        t.setGravity(Gravity.CENTER);
        return t;
    }

    private int dp(int v) {
        return Math.round(v * getResources().getDisplayMetrics().density);
    }
}
