package com.tvhome;

import android.content.Context;
import android.graphics.Bitmap;
import android.graphics.Canvas;
import android.graphics.Rect;
import android.graphics.Color;
import android.graphics.LinearGradient;
import android.graphics.Paint;
import android.graphics.RadialGradient;
import android.graphics.Shader;
import android.view.View;

/**
 * The Experimenting theme's flowing red and blue backdrop, ported from the
 * phone's PlasmaBackdrop (ios/.../Views/Experiments.swift): seven soft blobs on
 * wandering paths, stretched along their travel, each axis driven by a
 * {@link Gust} so they speed up and ease off at random-looking times without
 * ever jumping or reversing. A thin wash of the deep background on top is the
 * frost (thinner while Liquid glass puts the frost on the cards).
 *
 * Drawn into a bitmap an eighth of the screen's size and stretched up with
 * filtering: it's all soft gradients, so it looks the same, and the TV's GPU
 * fills 1/64 of the pixels (seven full-screen gradients dropped it to ~7 fps).
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

    private final Paint paint = new Paint(Paint.ANTI_ALIAS_FLAG);
    private final int background, deep;
    private final boolean glass;
    private final int[] colors;
    private final double[] radius, opacity, phase;
    private final Gust[] gx, gy, spin;
    private final Shader[] shaders;
    private float shaderSide = -1;
    private static final int SHRINK = 8;
    private Bitmap small;
    private Canvas smallCanvas;
    private final Paint blit = new Paint(Paint.FILTER_BITMAP_FLAG);
    private final Rect src = new Rect(), dst = new Rect();

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
        shaders = new Shader[n];
        for (int i = 0; i < n; i++) {
            double seed = i;
            gx[i] = new Gust(1 / (34 + seed * 4), seed);
            gy[i] = new Gust(1 / (27 + seed * 5), seed + 11.3);
            spin[i] = new Gust(1 / (60 + seed * 7), seed + 5.9);
            phase[i] = seed * 1.37;
        }
    }

    @Override
    protected void onDraw(Canvas screen) {
        int sw = Math.max(1, getWidth() / SHRINK), sh = Math.max(1, getHeight() / SHRINK);
        if (small == null || small.getWidth() != sw || small.getHeight() != sh) {
            small = Bitmap.createBitmap(sw, sh, Bitmap.Config.ARGB_8888);
            smallCanvas = new Canvas(small);
        }
        Canvas canvas = smallCanvas;
        float w = sw, h = sh;
        float side = Math.max(w, h);
        double t = (System.currentTimeMillis() / 1000.0) % 86_400;

        paint.setShader(new LinearGradient(0, 0, w, h, background, deep, Shader.TileMode.CLAMP));
        canvas.drawRect(0, 0, w, h, paint);

        if (side != shaderSide) {
            for (int i = 0; i < colors.length; i++) {
                float r = (float) (side * radius[i]);
                int c = colors[i];
                shaders[i] = new RadialGradient(0, 0, r,
                        new int[] {Theme.alpha(c, (int) (255 * opacity[i])), Theme.alpha(c, (int) (255 * opacity[i] * 0.35)), Theme.alpha(c, 0)},
                        new float[] {0f, 0.5f, 1f}, Shader.TileMode.CLAMP);
            }
            shaderSide = side;
        }
        for (int i = 0; i < colors.length; i++) {
            double px = 2 * Math.PI * gx[i].phase(t) + phase[i];
            double py = 2 * Math.PI * gy[i].phase(t) + phase[i] * 1.3;
            double x = 0.5 + 0.34 * Math.sin(px) + 0.12 * Math.sin(2.3 * px + 1.1);
            double y = 0.5 + 0.32 * Math.cos(py) + 0.12 * Math.cos(1.7 * py + 0.4);
            double dx = 0.34 * Math.cos(px) + 0.276 * Math.cos(2.3 * px + 1.1);
            double dy = -(0.32 * Math.sin(py) + 0.204 * Math.sin(1.7 * py + 0.4));
            double angle = Math.atan2(dy, dx) + 0.4 * Math.sin(2 * Math.PI * spin[i].phase(t));
            float r = (float) (side * radius[i]);
            canvas.save();
            canvas.translate((float) (x * w), (float) (y * h));
            canvas.rotate((float) Math.toDegrees(angle));
            canvas.scale(1.35f, 1 / 1.35f);
            paint.setShader(shaders[i]);
            canvas.drawCircle(0, 0, r, paint);
            canvas.restore();
        }
        paint.setShader(null);
        // The frost: a wash of the deep background evens the colour out.
        paint.setColor(Theme.alpha(deep, glass ? 60 : 110));
        canvas.drawRect(0, 0, w, h, paint);

        src.set(0, 0, sw, sh);
        dst.set(0, 0, getWidth(), getHeight());
        screen.drawBitmap(small, src, dst, blit);
        // ~24 fps is plenty for something this slow, and spares the TV.
        if (isAttachedToWindow()) postInvalidateDelayed(40);
    }
}
