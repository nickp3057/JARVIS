package com.tvhome;

import android.content.Context;
import android.graphics.Bitmap;
import android.graphics.Canvas;
import android.graphics.Color;
import android.graphics.LinearGradient;
import android.graphics.Paint;
import android.graphics.RadialGradient;
import android.graphics.Shader;
import android.view.View;

/**
 * The Experimenting theme's red and blue backdrop, ported from the phone's
 * PlasmaBackdrop (ios/.../Views/Experiments.swift): seven soft blobs placed on
 * their wandering paths (each axis a {@link Gust}), stretched along their
 * travel. On the TV it holds still (TV 9.1, Nick: "higher res but not
 * moving" so the TV isn't overloaded): drawn once, at full resolution, at the
 * same moment (t = 40 s) the phone shows with Reduce Motion, then just blitted.
 * Under Liquid glass it's vivid and unfrosted; otherwise a wash of the deep
 * background is the frost.
 */
final class PlasmaView extends View {
    /** Speed = rate·(1 + Σ a·cos(2πt/p + o)), integrated so position never jumps. */
    private static final class Gust {
        final double rate;
        final double[] amp = {0.42, 0.28, 0.18}, period = new double[3], offset = new double[3];

        Gust(double rate, double seed) {
            this.rate = rate;
            period[0] = 6.3 + seed % 3.1;
            period[1] = 13.7 + seed % 5.3;
            period[2] = 31.1 - seed % 7.7;
            offset[0] = seed * 1.7;
            offset[1] = seed * 2.9;
            offset[2] = seed * 0.6;
        }

        double phase(double t) {
            double extra = 0;
            for (int i = 0; i < 3; i++) extra += amp[i] * period[i] / (2 * Math.PI) * Math.sin(2 * Math.PI * t / period[i] + offset[i]);
            return rate * (t + extra);
        }
    }

    private static final double MOMENT = 40;
    private final Paint paint = new Paint(Paint.ANTI_ALIAS_FLAG | Paint.DITHER_FLAG);
    private final Paint blit = new Paint(Paint.FILTER_BITMAP_FLAG | Paint.DITHER_FLAG);
    private final int background, deep;
    private final boolean glass;
    private final int[] colors;
    private final double[] radius, opacity, phase;
    private final Gust[] gx, gy, spin;
    private Bitmap still;

    PlasmaView(Context context, Theme theme, boolean glass) {
        super(context);
        this.background = theme.background;
        this.deep = theme.deep;
        this.glass = glass;
        int red = theme.accent, ice = theme.reactor;
        int blue = Color.rgb(38, 97, 230); // Spider-Man's secondary (the TV palette has no slot for it)
        colors = new int[] {red, blue, red, ice, blue, red, blue};
        radius = new double[] {0.55, 0.65, 0.38, 0.40, 0.34, 0.28, 0.30};
        opacity = new double[] {0.60, 0.68, 0.46, 0.32, 0.50, 0.36, 0.40};
        int n = colors.length;
        phase = new double[n];
        gx = new Gust[n];
        gy = new Gust[n];
        spin = new Gust[n];
        for (int i = 0; i < n; i++) {
            double seed = i;
            gx[i] = new Gust(1 / (34 + seed * 4), seed);
            gy[i] = new Gust(1 / (27 + seed * 5), seed + 11.3);
            spin[i] = new Gust(1 / (60 + seed * 7), seed + 5.9);
            phase[i] = seed * 1.37;
        }
    }

    private Bitmap render(int width, int height) {
        Bitmap bitmap = Bitmap.createBitmap(width, height, Bitmap.Config.ARGB_8888);
        Canvas canvas = new Canvas(bitmap);
        float w = width, h = height, side = Math.max(w, h);
        double t = MOMENT;
        paint.setShader(new LinearGradient(0, 0, w, h, background, deep, Shader.TileMode.CLAMP));
        canvas.drawRect(0, 0, w, h, paint);
        for (int i = 0; i < colors.length; i++) {
            double px = 2 * Math.PI * gx[i].phase(t) + phase[i];
            double py = 2 * Math.PI * gy[i].phase(t) + phase[i] * 1.3;
            double x = 0.5 + 0.34 * Math.sin(px) + 0.12 * Math.sin(2.3 * px + 1.1);
            double y = 0.5 + 0.32 * Math.cos(py) + 0.12 * Math.cos(1.7 * py + 0.4);
            double dx = 0.34 * Math.cos(px) + 0.276 * Math.cos(2.3 * px + 1.1);
            double dy = -(0.32 * Math.sin(py) + 0.204 * Math.sin(1.7 * py + 0.4));
            double angle = Math.atan2(dy, dx) + 0.4 * Math.sin(2 * Math.PI * spin[i].phase(t));
            float r = (float) (side * radius[i]);
            int c = colors[i];
            if (glass) {
                // Vivid: stronger colour, a bright centre that holds, a quicker fade.
                double o = Math.min(0.95, opacity[i] * 1.4);
                paint.setShader(new RadialGradient(0, 0, r,
                        new int[] {a(c, o), a(c, o * 0.8), a(c, o * 0.25), a(c, 0)},
                        new float[] {0f, 0.3f, 0.65f, 1f}, Shader.TileMode.CLAMP));
            } else {
                paint.setShader(new RadialGradient(0, 0, r,
                        new int[] {a(c, opacity[i]), a(c, opacity[i] * 0.35), a(c, 0)},
                        new float[] {0f, 0.5f, 1f}, Shader.TileMode.CLAMP));
            }
            canvas.save();
            canvas.translate((float) (x * w), (float) (y * h));
            canvas.rotate((float) Math.toDegrees(angle));
            canvas.scale(1.35f, 1 / 1.35f);
            canvas.drawCircle(0, 0, r, paint);
            canvas.restore();
        }
        paint.setShader(null);
        if (!glass) {
            // The frost: a wash of the deep background evens the colour out.
            paint.setColor(Theme.alpha(deep, 110));
            canvas.drawRect(0, 0, w, h, paint);
        }
        return bitmap;
    }

    private static int a(int color, double opacity) {
        return Theme.alpha(color, (int) Math.round(255 * Math.max(0, Math.min(1, opacity))));
    }

    @Override
    protected void onDraw(Canvas screen) {
        int w = getWidth(), h = getHeight();
        if (w <= 0 || h <= 0) return;
        if (still == null || still.getWidth() != w || still.getHeight() != h) {
            if (still != null) still.recycle();
            still = render(w, h);
        }
        screen.drawBitmap(still, 0, 0, blit);
    }

    @Override
    protected void onDetachedFromWindow() {
        super.onDetachedFromWindow();
        if (still != null) still.recycle();
        still = null;
    }
}
