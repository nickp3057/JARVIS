package com.tvhome;

import android.content.Context;
import android.graphics.Canvas;
import android.graphics.DashPathEffect;
import android.graphics.LinearGradient;
import android.graphics.Paint;
import android.graphics.RadialGradient;
import android.graphics.RectF;
import android.graphics.Shader;
import android.os.SystemClock;
import android.view.View;

/**
 * Barn Door Protocol's JARVIS: the reactor gone hostile, filling the screen.
 * A heartbeat glow, heavy armour segments turning one way and a targeting
 * dial racing the other, two sweeping lock-on arcs, a jagged flickering core
 * and a scan line raking the room. Red only.
 */
final class RedAlertView extends View {
    private final Paint paint = new Paint(Paint.ANTI_ALIAS_FLAG);
    private final RectF oval = new RectF();
    private final int red, hot;
    private final float density;
    private final long start = SystemClock.uptimeMillis();

    RedAlertView(Context context, int red, int hot) {
        super(context);
        this.red = red;
        this.hot = hot;
        this.density = context.getResources().getDisplayMetrics().density;
    }

    /** Lub-dub every 1.4 s: two quick swells, then a pause. */
    private static float heartbeat(float t) {
        float p = t % 1.4f;
        float lub = (float) Math.exp(-Math.pow((p - 0.10f) / 0.07f, 2));
        float dub = 0.7f * (float) Math.exp(-Math.pow((p - 0.32f) / 0.08f, 2));
        return Math.max(lub, dub);
    }

    @Override
    protected void onDraw(Canvas canvas) {
        float w = getWidth(), h = getHeight();
        float t = (SystemClock.uptimeMillis() - start) / 1000f;
        float beat = heartbeat(t);
        float cx = w / 2f, cy = h * 0.465f;
        float r = h * 0.35f;
        paint.setPathEffect(null);

        // Heartbeat glow behind everything, and the edges falling to black.
        paint.setStyle(Paint.Style.FILL);
        paint.setShader(new RadialGradient(cx, cy, r * 1.9f,
                new int[] {Theme.alpha(red, Math.round(70 + 110 * beat)), Theme.alpha(red, 0)},
                null, Shader.TileMode.CLAMP));
        canvas.drawRect(0, 0, w, h, paint);
        paint.setShader(new RadialGradient(cx, h / 2f, Math.max(w, h) * 0.75f,
                new int[] {0x00000000, 0xD0000000}, new float[] {0.55f, 1f}, Shader.TileMode.CLAMP));
        canvas.drawRect(0, 0, w, h, paint);
        paint.setShader(null);

        // Scan line raking down the room every 3 s.
        float sy = h * ((t % 3f) / 3f);
        paint.setShader(new LinearGradient(0, sy - 40 * density, 0, sy,
                Theme.alpha(red, 0), Theme.alpha(red, 110), Shader.TileMode.CLAMP));
        canvas.drawRect(0, sy - 40 * density, w, sy, paint);
        paint.setShader(null);

        paint.setStyle(Paint.Style.STROKE);
        float scale = 1f + 0.03f * beat;
        canvas.save();
        canvas.scale(scale, scale, cx, cy);

        // Armour: twelve heavy segments, turning slowly.
        paint.setStrokeCap(Paint.Cap.BUTT);
        paint.setColor(Theme.alpha(red, 210));
        paint.setStrokeWidth(r * 0.07f);
        float seg = (float) (2 * Math.PI * r / 12);
        paint.setPathEffect(new DashPathEffect(new float[] {seg * 0.62f, seg * 0.38f}, 0));
        canvas.save();
        canvas.rotate(360f * (t % 30f) / 30f, cx, cy);
        canvas.drawCircle(cx, cy, r, paint);
        canvas.restore();

        // Targeting dial: ninety ticks racing the other way.
        float rd = r * 0.86f;
        float gap = (float) (2 * Math.PI * rd / 90);
        paint.setColor(Theme.alpha(hot, 150));
        paint.setStrokeWidth(r * 0.05f);
        paint.setPathEffect(new DashPathEffect(new float[] {1.5f * density, gap - 1.5f * density}, 0));
        canvas.save();
        canvas.rotate(-360f * (t % 6f) / 6f, cx, cy);
        canvas.drawCircle(cx, cy, rd, paint);
        canvas.restore();
        paint.setPathEffect(null);

        // Two lock-on arcs sweeping opposite ways.
        paint.setStrokeCap(Paint.Cap.ROUND);
        paint.setColor(hot);
        paint.setStrokeWidth(r * 0.03f);
        arc(canvas, cx, cy, r * 0.72f, 360f * (t % 2.4f) / 2.4f, 0.32f);
        paint.setColor(Theme.alpha(red, 200));
        arc(canvas, cx, cy, r * 0.62f, -360f * (t % 1.7f) / 1.7f, 0.22f);
        paint.setStrokeCap(Paint.Cap.BUTT);
        paint.setStrokeWidth(density * 1.5f);
        paint.setColor(Theme.alpha(red, 120));
        canvas.drawCircle(cx, cy, r * 0.52f, paint);

        // Crosshair notches at the four points.
        paint.setStrokeWidth(r * 0.02f);
        paint.setColor(hot);
        for (int i = 0; i < 4; i++) {
            canvas.save();
            canvas.rotate(90f * i, cx, cy);
            canvas.drawLine(cx, cy - r * 1.07f, cx, cy - r * 0.95f, paint);
            canvas.restore();
        }

        // The core: a hot glow and a jagged, flickering waveform.
        paint.setStyle(Paint.Style.FILL);
        paint.setShader(new RadialGradient(cx, cy, r * 0.45f,
                new int[] {Theme.alpha(hot, Math.round(90 + 120 * beat)), Theme.alpha(red, 0)},
                null, Shader.TileMode.CLAMP));
        canvas.drawCircle(cx, cy, r * 0.45f, paint);
        paint.setShader(null);
        paint.setColor(hot);
        int bars = 13;
        float glyph = r * 0.42f;
        float bar = glyph / (bars * 1.8f);
        float x = cx - (bars * bar * 1.8f - bar * 0.8f) / 2;
        for (int i = 0; i < bars; i++) {
            double n = Math.sin(t * 9 + i * 1.7) * Math.sin(t * 4.3 + i * 0.9);
            float half = glyph * (0.15f + 0.85f * (float) Math.abs(n)) * (1f - Math.abs(i - bars / 2f) / bars) / 2;
            oval.set(x, cy - half, x + bar, cy + half);
            canvas.drawRect(oval, paint);
            x += bar * 1.8f;
        }

        canvas.restore();
        if (isAttachedToWindow()) postInvalidateOnAnimation();
    }

    private void arc(Canvas canvas, float cx, float cy, float r, float rotation, float sweep) {
        oval.set(cx - r, cy - r, cx + r, cy + r);
        canvas.drawArc(oval, rotation, sweep * 360f, false, paint);
    }
}
