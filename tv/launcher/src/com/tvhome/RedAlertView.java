package com.tvhome;

import android.content.Context;
import android.graphics.Canvas;
import android.graphics.DashPathEffect;
import android.graphics.Paint;
import android.graphics.Path;
import android.graphics.RadialGradient;
import android.graphics.RectF;
import android.graphics.Shader;
import android.os.SystemClock;
import android.view.View;

/**
 * Barn Door Protocol's JARVIS: the reactor gone hostile, filling the screen.
 * Nothing pulses or flashes (Nick's call); it's menacing through shape and
 * slow, steady motion: heavy armour turning one way, a ring of teeth closing
 * the other, a racing targeting dial, two lock-on arcs, and at the centre a
 * slit-pupilled eye that slowly looks around the room. Red only.
 */
final class RedAlertView extends View {
    private final Paint paint = new Paint(Paint.ANTI_ALIAS_FLAG);
    private final RectF oval = new RectF();
    private final Path path = new Path();
    private final int red, hot;
    private final float density;
    private final long start = SystemClock.uptimeMillis();

    RedAlertView(Context context, int red, int hot) {
        super(context);
        this.red = red;
        this.hot = hot;
        this.density = context.getResources().getDisplayMetrics().density;
    }

    @Override
    protected void onDraw(Canvas canvas) {
        float w = getWidth(), h = getHeight();
        float t = (SystemClock.uptimeMillis() - start) / 1000f;
        float cx = w / 2f, cy = h * 0.465f;
        float r = h * 0.35f;
        paint.setPathEffect(null);

        // A steady red glow behind it, and the edges falling to black.
        paint.setStyle(Paint.Style.FILL);
        paint.setShader(new RadialGradient(cx, cy, r * 1.9f,
                new int[] {Theme.alpha(red, 120), Theme.alpha(red, 0)}, null, Shader.TileMode.CLAMP));
        canvas.drawRect(0, 0, w, h, paint);
        paint.setShader(new RadialGradient(cx, h / 2f, Math.max(w, h) * 0.75f,
                new int[] {0x00000000, 0xD0000000}, new float[] {0.55f, 1f}, Shader.TileMode.CLAMP));
        canvas.drawRect(0, 0, w, h, paint);
        paint.setShader(null);

        // Armour: twelve heavy segments, turning slowly.
        paint.setStyle(Paint.Style.STROKE);
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
        float rd = r * 0.88f;
        float gap = (float) (2 * Math.PI * rd / 90);
        paint.setColor(Theme.alpha(hot, 150));
        paint.setStrokeWidth(r * 0.05f);
        paint.setPathEffect(new DashPathEffect(new float[] {1.5f * density, gap - 1.5f * density}, 0));
        canvas.save();
        canvas.rotate(-360f * (t % 6f) / 6f, cx, cy);
        canvas.drawCircle(cx, cy, rd, paint);
        canvas.restore();
        paint.setPathEffect(null);

        // Teeth: a ring of fangs pointing in, closing slowly against the armour.
        paint.setStyle(Paint.Style.FILL);
        paint.setColor(Theme.alpha(red, 230));
        canvas.save();
        canvas.rotate(-360f * (t % 45f) / 45f, cx, cy);
        int teeth = 24;
        for (int i = 0; i < teeth; i++) {
            canvas.save();
            canvas.rotate(360f * i / teeth, cx, cy);
            float base = r * 0.78f, tip = r * (i % 2 == 0 ? 0.62f : 0.68f), half = r * 0.045f;
            path.reset();
            path.moveTo(cx - half, cy - base);
            path.lineTo(cx + half, cy - base);
            path.lineTo(cx, cy - tip);
            path.close();
            canvas.drawPath(path, paint);
            canvas.restore();
        }
        canvas.restore();

        // Two lock-on arcs sweeping opposite ways.
        paint.setStyle(Paint.Style.STROKE);
        paint.setStrokeCap(Paint.Cap.ROUND);
        paint.setColor(hot);
        paint.setStrokeWidth(r * 0.025f);
        arc(canvas, cx, cy, r * 0.56f, 360f * (t % 2.4f) / 2.4f, 0.3f);
        paint.setColor(Theme.alpha(red, 200));
        arc(canvas, cx, cy, r * 0.50f, -360f * (t % 1.7f) / 1.7f, 0.2f);

        // Crosshair notches at the four points.
        paint.setStrokeCap(Paint.Cap.BUTT);
        paint.setStrokeWidth(r * 0.02f);
        paint.setColor(hot);
        for (int i = 0; i < 4; i++) {
            canvas.save();
            canvas.rotate(90f * i, cx, cy);
            canvas.drawLine(cx, cy - r * 1.07f, cx, cy - r * 0.95f, paint);
            canvas.restore();
        }

        // The eye: an almond lid, a red iris and a slit pupil that slowly
        // looks from side to side.
        float ew = r * 0.42f, eh = r * 0.2f;
        path.reset();
        path.moveTo(cx - ew, cy);
        path.quadTo(cx, cy - eh * 2, cx + ew, cy);
        path.quadTo(cx, cy + eh * 2, cx - ew, cy);
        path.close();
        paint.setStyle(Paint.Style.FILL);
        paint.setShader(new RadialGradient(cx, cy, ew,
                new int[] {Theme.alpha(hot, 255), Theme.alpha(red, 200), 0xFF100000},
                new float[] {0f, 0.55f, 1f}, Shader.TileMode.CLAMP));
        canvas.drawPath(path, paint);
        paint.setShader(null);
        float look = (float) Math.sin(t * 2 * Math.PI / 7) * ew * 0.35f;
        paint.setColor(0xFF050000);
        oval.set(cx + look - r * 0.035f, cy - eh * 0.9f, cx + look + r * 0.035f, cy + eh * 0.9f);
        canvas.drawOval(oval, paint);
        paint.setStyle(Paint.Style.STROKE);
        paint.setStrokeWidth(r * 0.018f);
        paint.setColor(hot);
        canvas.drawPath(path, paint);

        if (isAttachedToWindow()) postInvalidateOnAnimation();
    }
    private void arc(Canvas canvas, float cx, float cy, float r, float rotation, float sweep) {
        oval.set(cx - r, cy - r, cx + r, cy + r);
        canvas.drawArc(oval, rotation, sweep * 360f, false, paint);
    }
}
