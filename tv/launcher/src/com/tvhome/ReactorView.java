package com.tvhome;

import android.content.Context;
import android.graphics.Canvas;
import android.graphics.DashPathEffect;
import android.graphics.Paint;
import android.graphics.RadialGradient;
import android.graphics.RectF;
import android.graphics.Shader;
import android.os.SystemClock;
import android.view.View;

/**
 * The JARVIS reactor mark, drawn and animated like the phone's
 * JarvisCoreMark (ios/.../Views/JarvisTheme.swift): a steady glow, a
 * sixty-tick dial drifting one way (40 s a turn), two arcs counter-rotating
 * (16 s and 10 s) and a waveform at the core. Sizes are fractions of the
 * mark, as on the phone; the dial only shows at 60 dp and up.
 */
final class ReactorView extends View implements Beat.Dancer {
    private final Paint paint = new Paint(Paint.ANTI_ALIAS_FLAG);
    private final RectF oval = new RectF();
    private final int color, core;
    private final float density;
    private final long start = SystemClock.uptimeMillis();
    /** 0..1 from Beat (the Back in Black easter egg); 0 is the normal mark. */
    private float level;

    @Override
    public void setLevel(float level) {
        this.level = level;
    }

    /** Waveform bar heights (fractions of the glyph height), like SF Symbols' "waveform". */
    private static final float[] WAVE = {0.28f, 0.55f, 0.85f, 1f, 0.62f, 0.4f, 0.75f, 0.5f, 0.3f};

    ReactorView(Context context, int color, int core) {
        super(context);
        this.color = color;
        this.core = core;
        this.density = context.getResources().getDisplayMetrics().density;
    }

    @Override
    protected void onDraw(Canvas canvas) {
        float size = Math.min(getWidth(), getHeight());
        float cx = getWidth() / 2f, cy = getHeight() / 2f;
        float t = (SystemClock.uptimeMillis() - start) / 1000f;
        paint.setPathEffect(null);
        canvas.save();
        float bounce = 1f + 0.12f * level;
        canvas.scale(bounce, bounce, cx, cy);
        // Under the music the glow flares and the rings spin faster.
        t += Beat.spin;

        // Glow (steady: he doesn't breathe), and the core's warm centre.
        paint.setStyle(Paint.Style.FILL);
        paint.setShader(new RadialGradient(cx, cy, size / 2,
                new int[] {Theme.alpha(color, Math.min(255, Math.round(56 + 150 * level))), Theme.alpha(color, 0)},
                new float[] {4 * density / (size / 2), 1f}, Shader.TileMode.CLAMP));
        canvas.drawCircle(cx, cy, size / 2, paint);
        paint.setShader(new RadialGradient(cx, cy, size * 0.22f,
                new int[] {Theme.alpha(core, 41), Theme.alpha(core, 0)}, null, Shader.TileMode.CLAMP));
        canvas.drawCircle(cx, cy, size * 0.22f, paint);
        paint.setShader(null);

        paint.setStyle(Paint.Style.STROKE);
        paint.setStrokeCap(Paint.Cap.BUTT);
        paint.setColor(Theme.alpha(color, 38));
        paint.setStrokeWidth(density);
        canvas.drawCircle(cx, cy, size / 2 - 2 * density, paint);

        if (size >= 60 * density) {
            // HUD dial: sixty ticks drifting slowly the other way.
            float r = size * 0.46f;
            float gap = (float) (2 * Math.PI * r / 60);
            paint.setColor(Theme.alpha(color, 71));
            paint.setStrokeWidth(size * 0.025f);
            paint.setPathEffect(new DashPathEffect(new float[] {density, gap - density}, 0));
            canvas.save();
            canvas.rotate(-360f * (t % 40f) / 40f, cx, cy);
            canvas.drawCircle(cx, cy, r, paint);
            canvas.restore();
            paint.setPathEffect(null);
        }

        paint.setStrokeCap(Paint.Cap.ROUND);
        paint.setColor(Theme.alpha(color, 153));
        paint.setStrokeWidth(Math.max(1.5f * density, size / 56));
        arc(canvas, cx, cy, size / 2 - size * 0.13f, -60f + 360f * (t % 16f) / 16f, 0.05f, 0.82f);

        paint.setStrokeCap(Paint.Cap.BUTT);
        paint.setColor(Theme.alpha(color, 77));
        paint.setStrokeWidth(density);
        arc(canvas, cx, cy, size / 2 - size * 0.21f, 90f - 360f * (t % 10f) / 10f, 0.12f, 0.92f);

        // Waveform glyph at the core.
        paint.setStyle(Paint.Style.FILL);
        paint.setColor(color);
        float glyph = size * 0.25f;
        float bar = glyph / (WAVE.length * 1.9f);
        float x = cx - (WAVE.length * bar * 1.9f - bar * 0.9f) / 2;
        for (float h : WAVE) {
            float half = glyph * h / 2;
            oval.set(x, cy - half, x + bar, cy + half);
            canvas.drawRoundRect(oval, bar / 2, bar / 2, paint);
            x += bar * 1.9f;
        }

        canvas.restore();
        if (isAttachedToWindow()) postInvalidateOnAnimation();
    }

    /** SwiftUI-style trimmed circle: from/to are fractions of a turn from 3 o'clock, then rotated. */
    private void arc(Canvas canvas, float cx, float cy, float r, float rotation, float from, float to) {
        oval.set(cx - r, cy - r, cx + r, cy + r);
        canvas.drawArc(oval, rotation + from * 360f, (to - from) * 360f, false, paint);
    }
}
