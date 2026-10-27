package com.tvhome;

import android.animation.ValueAnimator;
import android.content.Context;
import android.graphics.Canvas;
import android.graphics.Paint;
import android.graphics.RadialGradient;
import android.graphics.RectF;
import android.graphics.Shader;
import android.view.View;

/** The arc-reactor mark: segmented rings around a softly breathing core. */
final class ReactorView extends View {
    private final Paint paint = new Paint(Paint.ANTI_ALIAS_FLAG);
    private final RectF oval = new RectF();
    private final int color;
    private float glow = 1f;
    private ValueAnimator pulse;

    ReactorView(Context context, int color) {
        super(context);
        this.color = color;
    }

    @Override
    protected void onAttachedToWindow() {
        super.onAttachedToWindow();
        pulse = ValueAnimator.ofFloat(0.7f, 1f);
        pulse.setDuration(2400);
        pulse.setRepeatMode(ValueAnimator.REVERSE);
        pulse.setRepeatCount(ValueAnimator.INFINITE);
        pulse.addUpdateListener(a -> {
            glow = (float) a.getAnimatedValue();
            invalidate();
        });
        pulse.start();
    }

    @Override
    protected void onDetachedFromWindow() {
        if (pulse != null) pulse.cancel();
        super.onDetachedFromWindow();
    }

    @Override
    protected void onDraw(Canvas canvas) {
        float cx = getWidth() / 2f, cy = getHeight() / 2f;
        float r = Math.min(cx, cy);

        paint.setShader(new RadialGradient(cx, cy, r,
                new int[] {Theme.alpha(color, Math.round(90 * glow)), Theme.alpha(color, 0)},
                null, Shader.TileMode.CLAMP));
        paint.setStyle(Paint.Style.FILL);
        canvas.drawCircle(cx, cy, r, paint);
        paint.setShader(null);

        paint.setStyle(Paint.Style.STROKE);
        paint.setColor(Theme.alpha(color, 200));
        paint.setStrokeWidth(r * 0.06f);
        canvas.drawCircle(cx, cy, r * 0.86f, paint);

        // Ten coil segments.
        paint.setStrokeWidth(r * 0.16f);
        paint.setColor(Theme.alpha(color, Math.round(255 * glow)));
        float ring = r * 0.62f;
        oval.set(cx - ring, cy - ring, cx + ring, cy + ring);
        for (int i = 0; i < 10; i++) canvas.drawArc(oval, i * 36f + 4f, 28f, false, paint);

        paint.setStyle(Paint.Style.FILL);
        paint.setShader(new RadialGradient(cx, cy, r * 0.34f,
                new int[] {0xFFFFFFFF, color}, null, Shader.TileMode.CLAMP));
        canvas.drawCircle(cx, cy, r * 0.30f * (0.92f + 0.08f * glow), paint);
        paint.setShader(null);
    }
}
