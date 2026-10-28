package com.tvhome;

import android.graphics.Color;

/**
 * The phone's palettes (ios/.../Views/JarvisTheme.swift, dark variants), so
 * the TV and the companion look like one JARVIS. Core says which one is
 * active (JARVIS_THEME in /api/glance).
 */
final class Theme {
    final int accent, reactor, warning, critical, success;
    final int background, deep, panel, raised;
    final int text = Color.WHITE;
    final int dim;

    private Theme(float[] accent, float[] reactor, float[] warning, float[] critical,
            float[] success, float[] background, float[] deep, float[] panel, float[] raised) {
        this.accent = rgb(accent);
        this.reactor = rgb(reactor);
        this.warning = rgb(warning);
        this.critical = rgb(critical);
        this.success = rgb(success);
        this.background = rgb(background);
        this.deep = rgb(deep);
        this.panel = rgb(panel);
        this.raised = rgb(raised);
        this.dim = Color.argb(150, 255, 255, 255);
    }

    private static final float[] GOLD = {1.0f, 0.77f, 0.32f};
    private static final float[] RED_WARNING = {1.0f, 0.50f, 0.18f};
    private static final float[] RED_CRITICAL = {1.0f, 0.22f, 0.27f};
    private static final float[] ICE = {0.55f, 0.90f, 1.0f};
    private static final float[][] RED_SURFACES = {
            {0.075f, 0.035f, 0.035f}, {0.025f, 0.012f, 0.014f},
            {0.12f, 0.065f, 0.06f}, {0.17f, 0.09f, 0.08f}};

    static final Theme ARC = new Theme(
            new float[] {0.25f, 0.85f, 0.95f}, new float[] {0.25f, 0.85f, 0.95f},
            new float[] {1.0f, 0.73f, 0.30f}, new float[] {1.0f, 0.36f, 0.38f},
            new float[] {0.30f, 0.88f, 0.62f},
            new float[] {0.025f, 0.045f, 0.075f}, new float[] {0.008f, 0.018f, 0.035f},
            new float[] {0.055f, 0.085f, 0.125f}, new float[] {0.075f, 0.12f, 0.17f});
    static final Theme MARK_III = new Theme(GOLD, GOLD, RED_WARNING, RED_CRITICAL, ICE,
            RED_SURFACES[0], RED_SURFACES[1], RED_SURFACES[2], RED_SURFACES[3]);
    static final Theme HOT_ROD = new Theme(GOLD, new float[] {0.35f, 0.88f, 1.0f},
            RED_WARNING, RED_CRITICAL, ICE,
            RED_SURFACES[0], RED_SURFACES[1], RED_SURFACES[2], RED_SURFACES[3]);

    static Theme named(String name) {
        if ("markIII".equals(name)) return MARK_III;
        if ("hotRod".equals(name)) return HOT_ROD;
        return ARC;
    }

    int severity(String severity) {
        if ("critical".equals(severity)) return critical;
        if ("info".equals(severity)) return success;
        if ("reply".equals(severity)) return accent; // JARVIS answering the remote's mic
        return warning;
    }

    static int alpha(int color, int alpha) {
        return Color.argb(alpha, Color.red(color), Color.green(color), Color.blue(color));
    }

    private static int rgb(float[] c) {
        return Color.rgb(Math.round(c[0] * 255), Math.round(c[1] * 255), Math.round(c[2] * 255));
    }
}
