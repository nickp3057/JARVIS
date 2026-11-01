package com.tvhome;

import android.content.Context;
import android.graphics.Canvas;
import android.graphics.Color;
import android.graphics.Paint;
import android.graphics.PorterDuff;
import android.graphics.PorterDuffXfermode;
import android.graphics.RadialGradient;
import android.graphics.Shader;
import android.os.SystemClock;
import android.view.View;

/**
 * The neural core on the TV, drawn like the phone's NeuralCore
 * (ios/.../Views/Experiments.swift): wires batched by kind and depth, gold
 * impulses, box nodes that turn to diamonds as it bristles. Shown instead of
 * the classic reactor when Core says {@code core: "neural"} (Experimenting
 * picked, Neural core switch on), and in the alert mood as Barn Door's
 * full-screen reactor. All views share {@link NeuralNet#SHARED}, so a mood
 * change (Barn Door recreating the screen) morphs instead of cutting.
 */
final class NeuralView extends View implements Beat.Dancer {
    private final Paint paint = new Paint(Paint.ANTI_ALIAS_FLAG);
    private final NeuralNet net = NeuralNet.SHARED;
    private final int mood;
    private final int structure;
    private final float density;
    private final double[][] pose;
    private final float[] px, py, depth;
    private float[][] lines = new float[12][];
    private final int[] lineCount = new int[12];
    private float level;

    // Thinking gold, alert reds (the phone's colours).
    private static final int GOLD = Color.rgb(255, 173, 56), HOT = Color.rgb(255, 230, 158);
    private static final int ALERT_LINE = Color.rgb(255, 46, 41), ALERT_IMPULSE = Color.rgb(255, 107, 71),
            ALERT_HOT = Color.rgb(255, 204, 179);
    private static final int WATCH_LINE = Color.rgb(51, 217, 115), WATCH_IMPULSE = Color.rgb(140, 255, 179),
            WATCH_HOT = Color.rgb(217, 255, 230);

    /** {@code structure} is the palette's reactor colour (thinking wires). */
    NeuralView(Context context, int structure, int mood) {
        super(context);
        this.structure = structure;
        this.mood = mood;
        this.density = context.getResources().getDisplayMetrics().density;
        pose = new double[net.count][3];
        px = new float[net.count];
        py = new float[net.count];
        depth = new float[net.count];
        for (int i = 0; i < 12; i++) lines[i] = new float[64];
    }

    @Override
    public void setLevel(float level) {
        this.level = level;
    }

    private static int mixColor(int a, int b, int c, double[] w) {
        return Color.rgb(
                (int) Math.round(Color.red(a) * w[0] + Color.red(b) * w[1] + Color.red(c) * w[2]),
                (int) Math.round(Color.green(a) * w[0] + Color.green(b) * w[1] + Color.green(c) * w[2]),
                (int) Math.round(Color.blue(a) * w[0] + Color.blue(b) * w[1] + Color.blue(c) * w[2]));
    }

    private static int alpha(int color, double opacity) {
        return Theme.alpha(color, (int) Math.max(0, Math.min(255, Math.round(opacity * 255))));
    }

    private static int bucket(float d) {
        return d < 0.45f ? 0 : (d < 0.75f ? 1 : 2);
    }

    private void addLine(int slot, float x1, float y1, float x2, float y2) {
        int n = lineCount[slot];
        if (n + 4 > lines[slot].length) lines[slot] = java.util.Arrays.copyOf(lines[slot], lines[slot].length * 2);
        float[] l = lines[slot];
        l[n] = x1;
        l[n + 1] = y1;
        l[n + 2] = x2;
        l[n + 3] = y2;
        lineCount[slot] = n + 4;
    }

    @Override
    protected void onDraw(Canvas canvas) {
        net.step(SystemClock.uptimeMillis() / 1000.0, mood, level > 0.05f);
        float w = getWidth(), h = getHeight();
        float side = Math.min(w, h);
        float cx = w / 2f, cy = h / 2f;
        canvas.save();
        float bounce = 1f + 0.12f * level; // Back in Black
        canvas.scale(bounce, bounce, cx, cy);

        double[] m = net.mix[2];
        int line = mixColor(structure, WATCH_LINE, ALERT_LINE, m);
        int impulse = mixColor(GOLD, WATCH_IMPULSE, ALERT_IMPULSE, m);
        int hot = mixColor(HOT, WATCH_HOT, ALERT_HOT, m);
        double lift = 0.3 * m[1];
        double unit = side * 1.15 / 1.6;
        net.pose(pose);
        for (int i = 0; i < net.count; i++) {
            double scale = side * 1.15 / (1.6 - pose[i][2] * 0.6);
            px[i] = (float) (cx + pose[i][0] * scale);
            py[i] = (float) (cy + pose[i][1] * scale);
            depth[i] = (float) Math.max(0.15, Math.min(1, pose[i][2] + 0.55 + lift));
        }
        float stroke = Math.max(0.6f * density, side / 240f);

        // Halo.
        paint.setStyle(Paint.Style.FILL);
        paint.setShader(new RadialGradient(cx, cy, side * 0.48f,
                new int[] {alpha(line, 0.15 + 0.12 * m[2]), alpha(line, 0)}, null, Shader.TileMode.CLAMP));
        canvas.drawCircle(cx, cy, side * 0.48f, paint);
        paint.setShader(null);

        // The eye's pupil.
        double seeing = net.mix[0][1];
        if (seeing > 0.01) {
            float ex = (float) (cx + net.look[0] * unit), ey = (float) (cy + net.look[1] * unit);
            float r = (float) (0.075 * unit);
            paint.setShader(new RadialGradient(ex, ey, r * 4, new int[] {alpha(impulse, 0.22 * seeing), alpha(impulse, 0)},
                    new float[] {0.25f, 1f}, Shader.TileMode.CLAMP));
            canvas.drawCircle(ex, ey, r * 4, paint);
            paint.setShader(null);
            paint.setColor(alpha(Color.rgb(3, 4, 10), 0.9 * seeing));
            canvas.drawCircle(ex, ey, r, paint);
        }

        paint.setXfermode(new PorterDuffXfermode(PorterDuff.Mode.ADD));
        // Glass shells and light in the core while thinking.
        double layered = net.mix[1][0];
        if (layered > 0.01) {
            float cr = (float) (0.2 * unit);
            paint.setShader(new RadialGradient(cx, cy, cr, new int[] {alpha(impulse, 0.20 * layered), alpha(impulse, 0)},
                    null, Shader.TileMode.CLAMP));
            canvas.drawCircle(cx, cy, cr, paint);
            paint.setShader(null);
            paint.setStyle(Paint.Style.STROKE);
            paint.setStrokeWidth(stroke);
            double[] shellAlpha = {0.20, 0.14, 0.10};
            for (int k = 0; k < 3; k++) {
                paint.setColor(alpha(line, shellAlpha[k] * layered));
                canvas.drawCircle(cx, cy, (float) (NeuralNet.SHELL_RADII[k] * unit), paint);
            }
        }
        // Spikes out from the middle in the alert.
        paint.setStyle(Paint.Style.STROKE);
        paint.setStrokeCap(Paint.Cap.ROUND);
        if (m[2] > 0.01) {
            paint.setStrokeWidth(stroke * 1.6f);
            for (int i = 0; i < net.count; i++) {
                if (net.layer[i] != 2 || net.spike[i] <= 1.3) continue;
                float rx = cx + (px[i] - cx) * 0.2f, ry = cy + (py[i] - cy) * 0.2f;
                int c = alpha(line, (0.5 + 0.5 * depth[i]) * m[2]);
                paint.setShader(new android.graphics.LinearGradient(rx, ry, px[i], py[i], alpha(line, 0), c, Shader.TileMode.CLAMP));
                canvas.drawLine(rx, ry, px[i], py[i], paint);
            }
            paint.setShader(null);
        }

        // Wires: 4 kinds x 3 depths, one drawLines each.
        java.util.Arrays.fill(lineCount, 0);
        double[] weight = {1.25 - 0.85 * m[2], 0.7 - 0.7 * m[2], 0.8 - 0.3 * m[2] + 0.5 * m[1], 0.9};
        paint.setStrokeCap(Paint.Cap.BUTT);
        for (int e = 0; e < net.edgeA.length; e++) {
            int a = net.edgeA[e], b = net.edgeB[e];
            addLine(net.edgeKind[e] * 3 + bucket((depth[a] + depth[b]) / 2), px[a], py[a], px[b], py[b]);
        }
        double[] bucketDepth = {0.3, 0.6, 0.88};
        paint.setStrokeWidth(stroke);
        for (int slot = 0; slot < 12; slot++) {
            if (lineCount[slot] == 0) continue;
            double wgt = weight[slot / 3];
            if (wgt <= 0.01) continue;
            paint.setColor(alpha(line, (0.10 + 0.32 * bucketDepth[slot % 3]) * wgt));
            canvas.drawLines(lines[slot], 0, lineCount[slot], paint);
        }
        // Warm wires near firing nodes.
        paint.setStrokeWidth(stroke * 1.4f);
        for (int e = 0; e < net.edgeA.length; e++) {
            int a = net.edgeA[e], b = net.edgeB[e];
            double heat = Math.max(net.glow[a], net.glow[b]);
            if (heat <= 0.05 || net.edgeKind[e] == NeuralNet.ORBIT) continue;
            paint.setColor(alpha(impulse, 0.5 * heat * Math.min(1, weight[net.edgeKind[e]])));
            canvas.drawLine(px[a], py[a], px[b], py[b], paint);
        }

        // Impulses: a short trail and a bright head.
        paint.setStrokeCap(Paint.Cap.ROUND);
        paint.setStrokeWidth(stroke * 2);
        for (NeuralNet.Signal s : net.signals) {
            float f = (float) Math.max(0, Math.min(1, s.progress));
            float hx = px[s.from] + (px[s.to] - px[s.from]) * f, hy = py[s.from] + (py[s.to] - py[s.from]) * f;
            float tf = Math.max(0, f - 0.4f);
            float tx = px[s.from] + (px[s.to] - px[s.from]) * tf, ty = py[s.from] + (py[s.to] - py[s.from]) * tf;
            float d = depth[s.from] + (depth[s.to] - depth[s.from]) * f;
            double strength = s.kind == NeuralNet.PACKET ? 0.7 : 1;
            paint.setStyle(Paint.Style.STROKE);
            paint.setColor(alpha(impulse, 0.6 * d * strength));
            canvas.drawLine(tx, ty, hx, hy, paint);
            paint.setStyle(Paint.Style.FILL);
            paint.setColor(alpha(hot, d * strength));
            canvas.drawCircle(hx, hy, stroke * (s.kind == NeuralNet.THOUGHT ? 2.6f : 2f), paint);
        }

        // Nodes: boxes turning into diamonds as it bristles.
        paint.setStyle(Paint.Style.FILL);
        for (int i = 0; i < net.count; i++) {
            boolean ring = net.layer[i] >= 3;
            float box = (float) (Math.max(1.1f * density, side / 105f) * (0.6 + 0.6 * depth[i])
                    * (ring ? 0.7 : 1) * (1 + net.glow[i] * 0.8));
            int b = bucket(depth[i]);
            paint.setColor(alpha(line, 0.25 + 0.55 * bucketDepth[b]));
            canvas.save();
            canvas.rotate((float) (45 * net.mix[net.layer[i]][2]), px[i], py[i]);
            canvas.drawRect(px[i] - box / 2, py[i] - box / 2, px[i] + box / 2, py[i] + box / 2, paint);
            canvas.restore();
            double g = net.glow[i];
            if (g > 0.02) {
                paint.setColor(alpha(hot, g * depth[i]));
                canvas.drawCircle(px[i], py[i], box / 2, paint);
                paint.setColor(alpha(impulse, 0.18 * g * depth[i]));
                canvas.drawCircle(px[i], py[i], box * 2.2f, paint);
                paint.setColor(alpha(impulse, 0.10 * g * depth[i]));
                canvas.drawCircle(px[i], py[i], box * 3.2f, paint);
            }
        }
        paint.setXfermode(null);
        canvas.restore();
        if (isAttachedToWindow()) postInvalidateOnAnimation();
    }
}
