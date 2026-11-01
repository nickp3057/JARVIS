package com.tvhome;

import android.content.Context;
import android.media.audiofx.Visualizer;
import android.os.Handler;
import android.os.Looper;
import android.os.SystemClock;
import android.util.Log;

/**
 * Easter egg: while "Back in Black" plays, the reactor bounces to it. Reads
 * the loudness of what the TV is playing through the output-mix Visualizer
 * (session 0; RECORD_AUDIO, granted by deploy.sh), polled every frame. If
 * the TV only hands back silence (some do for app audio), it falls back to
 * the song's own tempo, 94 BPM. Drives ReactorView's bounce and glow, and
 * {@link #spin} makes the rings run faster on the loud bits.
 */
final class Beat {
    /** A reactor that can bounce: ReactorView or NeuralView. */
    interface Dancer {
        void setLevel(float level);
    }

    private static final String TAG = "TvHomeBeat";
    private static final float BPM = 94f;
    private static final Handler MAIN = new Handler(Looper.getMainLooper());
    private static final Runnable TICK = Beat::tick;

    /** Extra seconds added to the reactor's clock: the rings spin faster with the music. */
    static volatile float spin;

    private static Visualizer viz;
    private static byte[] wave;
    private static Dancer view;
    private static boolean fallback;
    private static float smooth, peak;
    private static long startedAt;

    private Beat() {}

    static void start(Context c, Dancer v) {
        if (view == v) return;
        stop();
        view = v;
        startedAt = SystemClock.uptimeMillis();
        fallback = false;
        peak = 0;
        smooth = 0;
        try {
            viz = new Visualizer(0);
            viz.setEnabled(false);
            viz.setCaptureSize(Visualizer.getCaptureSizeRange()[0]);
            viz.setScalingMode(Visualizer.SCALING_MODE_NORMALIZED); // works at any volume
            wave = new byte[viz.getCaptureSize()];
            viz.setEnabled(true);
        } catch (Exception | Error e) {
            Log.i(TAG, "no visualizer, keeping time instead: " + e);
            viz = null;
            fallback = true;
        }
        MAIN.post(TICK);
    }

    static void stop() {
        MAIN.removeCallbacks(TICK);
        if (viz != null) {
            try {
                viz.release();
            } catch (Exception ignored) {
            }
            viz = null;
        }
        if (view != null) view.setLevel(0);
        view = null;
    }

    private static void tick() {
        if (view == null) return;
        long t = SystemClock.uptimeMillis() - startedAt;
        float target = 0;
        if (!fallback && viz != null) {
            try {
                if (viz.getWaveForm(wave) == Visualizer.SUCCESS) {
                    double sum = 0;
                    for (byte b : wave) {
                        double s = ((b & 0xFF) - 128) / 128.0;
                        sum += s * s;
                    }
                    double rms = Math.sqrt(sum / wave.length);
                    target = (float) Math.max(0, Math.min(1, (rms - 0.05) / 0.35));
                    peak = Math.max(peak, target);
                }
            } catch (Exception e) {
                fallback = true;
            }
            if (t > 3000 && peak < 0.03f) {
                Log.i(TAG, "visualizer is silent here, keeping time instead");
                fallback = true;
            }
        }
        if (fallback) {
            float phase = (t / 1000f * BPM / 60f) % 1f;
            target = (float) Math.exp(-phase * 6);
        }
        // Snap up on a hit, ease down after it.
        smooth = target > smooth ? target : smooth * 0.86f + target * 0.14f;
        view.setLevel(smooth);
        spin += 0.016f * 2.5f * smooth;
        MAIN.postDelayed(TICK, 16);
    }
}
