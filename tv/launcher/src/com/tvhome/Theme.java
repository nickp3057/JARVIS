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
    static final Theme HOT_ROD = new Theme(GOLD, new float[] {0.35f, 0.88f, 1.0f},
            RED_WARNING, RED_CRITICAL, ICE,
            RED_SURFACES[0], RED_SURFACES[1], RED_SURFACES[2], RED_SURFACES[3]);

    static final Theme MALIBU = new Theme(
            new float[] {1.0f, 0.58f, 0.28f}, new float[] {0.35f, 0.88f, 1.0f},
            new float[] {1.0f, 0.78f, 0.3f}, new float[] {1.0f, 0.33f, 0.36f},
            new float[] {0.35f, 0.88f, 0.7f}, new float[] {0.04f, 0.05f, 0.1f},
            new float[] {0.015f, 0.02f, 0.05f}, new float[] {0.08f, 0.1f, 0.17f},
            new float[] {0.11f, 0.14f, 0.23f});
    static final Theme CRIMSON = new Theme(
            new float[] {0.95f, 0.28f, 0.33f}, new float[] {0.8f, 0.9f, 1.0f},
            new float[] {1.0f, 0.68f, 0.25f}, new float[] {1.0f, 0.45f, 0.25f},
            new float[] {0.4f, 0.85f, 0.6f}, new float[] {0.06f, 0.06f, 0.07f},
            new float[] {0.02f, 0.02f, 0.025f}, new float[] {0.1f, 0.1f, 0.11f},
            new float[] {0.14f, 0.14f, 0.155f});
    static final Theme MARK_85 = new Theme(
            new float[] {1.0f, 0.8f, 0.4f}, new float[] {0.35f, 0.88f, 1.0f},
            new float[] {1.0f, 0.58f, 0.22f}, new float[] {1.0f, 0.3f, 0.34f},
            new float[] {0.5f, 0.88f, 0.95f}, new float[] {0.035f, 0.045f, 0.085f},
            new float[] {0.012f, 0.016f, 0.035f}, new float[] {0.07f, 0.085f, 0.14f},
            new float[] {0.1f, 0.12f, 0.19f});

    static final Theme SPIDER_MAN = new Theme(
            new float[] {0.93f, 0.18f, 0.22f}, new float[] {0.55f, 0.75f, 1.0f},
            new float[] {1.0f, 0.7f, 0.25f}, new float[] {1.0f, 0.42f, 0.3f},
            new float[] {0.4f, 0.85f, 0.65f}, new float[] {0.03f, 0.04f, 0.09f},
            new float[] {0.01f, 0.015f, 0.04f}, new float[] {0.06f, 0.08f, 0.16f},
            new float[] {0.09f, 0.11f, 0.22f});
    static final Theme MCQUEEN = new Theme(
            new float[] {1.0f, 0.82f, 0.15f}, new float[] {0.96f, 0.2f, 0.17f},
            new float[] {1.0f, 0.6f, 0.18f}, new float[] {1.0f, 0.36f, 0.38f},
            new float[] {0.45f, 0.88f, 0.55f}, new float[] {0.075f, 0.075f, 0.08f},
            new float[] {0.03f, 0.03f, 0.035f}, new float[] {0.12f, 0.12f, 0.13f},
            new float[] {0.17f, 0.17f, 0.18f});
    static final Theme BARN_DOOR = new Theme(
            new float[] {1.0f, 0.18f, 0.16f}, new float[] {1.0f, 0.18f, 0.16f},
            new float[] {1.0f, 0.45f, 0.3f}, new float[] {1.0f, 0.82f, 0.8f},
            new float[] {1.0f, 0.55f, 0.5f}, new float[] {0.1f, 0.0f, 0.0f},
            new float[] {0.04f, 0.0f, 0.0f}, new float[] {0.16f, 0.02f, 0.02f},
            new float[] {0.22f, 0.03f, 0.03f});

    static Theme named(String name) {
        if ("hotRod".equals(name)) return HOT_ROD;
        if ("malibu".equals(name)) return MALIBU;
        if ("crimson".equals(name)) return CRIMSON;
        if ("mark85".equals(name)) return MARK_85;
        if ("spiderMan".equals(name) || "experimenting".equals(name)) return SPIDER_MAN; // the iOS lab theme
        if ("mcqueen".equals(name)) return MCQUEEN;
        if ("barnDoor".equals(name)) return BARN_DOOR; // Barn Door Protocol, never a pick
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
