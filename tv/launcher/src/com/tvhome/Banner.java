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

/**
 * A JARVIS alert card in the top-right corner, drawn over any app
 * (SYSTEM_ALERT_WINDOW). It never takes focus, so the remote keeps working
 * in Plex underneath. One at a time; a new alert replaces the old one.
 */
final class Banner {
    private static final Handler MAIN = new Handler(Looper.getMainLooper());
    private static View current;

    private Banner() {}

    static void show(Context context, Theme theme, String severity, String title, String message) {
        MAIN.post(() -> {
            Context c = context.getApplicationContext();
            WindowManager wm = c.getSystemService(WindowManager.class);
            dismiss(wm);
            float d = c.getResources().getDisplayMetrics().density;
            int tone = theme.severity(severity);

            LinearLayout card = new LinearLayout(c);
            card.setOrientation(LinearLayout.HORIZONTAL);
            card.setPadding(px(d, 16), px(d, 14), px(d, 20), px(d, 14));
            GradientDrawable bg = new GradientDrawable();
            bg.setColor(Theme.alpha(theme.raised, 240));
            bg.setCornerRadius(px(d, 14));
            bg.setStroke(px(d, 1), Theme.alpha(tone, 160));
            card.setBackground(bg);

            ReactorView mark = new ReactorView(c, tone, theme.success);
            LinearLayout.LayoutParams markLp = new LinearLayout.LayoutParams(px(d, 36), px(d, 36));
            markLp.setMargins(0, 0, px(d, 14), 0);
            markLp.gravity = Gravity.CENTER_VERTICAL;
            card.addView(mark, markLp);

            LinearLayout text = new LinearLayout(c);
            text.setOrientation(LinearLayout.VERTICAL);
            TextView eyebrow = label(c, "JARVIS", 10, Theme.alpha(tone, 220));
            eyebrow.setLetterSpacing(0.25f);
            text.addView(eyebrow);
            TextView head = label(c, title, 16, theme.text);
            head.setTypeface(Typeface.create("sans-serif-medium", Typeface.NORMAL));
            text.addView(head);
            boolean reply = "reply".equals(severity);
            if (!message.isEmpty()) {
                TextView body = label(c, message, reply ? 15 : 13, reply ? theme.text : theme.dim);
                body.setMaxLines(reply ? 7 : 2);
                body.setEllipsize(TextUtils.TruncateAt.END);
                text.addView(body);
            }
            card.addView(text);

            WindowManager.LayoutParams lp = new WindowManager.LayoutParams(
                    px(d, reply ? 460 : 380), WindowManager.LayoutParams.WRAP_CONTENT,
                    WindowManager.LayoutParams.TYPE_APPLICATION_OVERLAY,
                    WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE
                            | WindowManager.LayoutParams.FLAG_NOT_TOUCHABLE,
                    PixelFormat.TRANSLUCENT);
            lp.gravity = Gravity.TOP | Gravity.END;
            lp.x = px(d, 32);
            lp.y = px(d, 28);
            try {
                wm.addView(card, lp);
            } catch (Exception e) {
                return; // overlay permission revoked; the home screen still lists it
            }
            current = card;
            card.setAlpha(0f);
            card.setTranslationX(px(d, 40));
            card.animate().alpha(1f).translationX(0).setDuration(250).start();
            View shown = card;
            MAIN.postDelayed(() -> {
                if (current == shown) shown.animate().alpha(0f).setDuration(400)
                        .withEndAction(() -> dismiss(wm)).start();
            }, reply ? Math.min(30_000, 6_000 + message.length() * 70L)
                    : "critical".equals(severity) ? 20_000 : 10_000);
        });
    }

    private static void dismiss(WindowManager wm) {
        if (current == null) return;
        try {
            wm.removeView(current);
        } catch (Exception ignored) {
        }
        current = null;
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
