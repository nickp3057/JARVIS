package com.tvhome;

import android.content.Context;
import android.graphics.Canvas;
import android.graphics.Paint;
import android.graphics.Path;
import android.view.View;

/** Previous / play / pause / next, drawn (TV fonts lack the media glyphs). */
final class MediaIcon extends View {
    static final int PREVIOUS = 0, PLAY = 1, PAUSE = 2, NEXT = 3;
    private final Paint paint = new Paint(Paint.ANTI_ALIAS_FLAG);
    private final Path path = new Path();
    private int kind;

    MediaIcon(Context context, int kind, int color) {
        super(context);
        this.kind = kind;
        paint.setColor(color);
    }

    void setKind(int kind) {
        if (this.kind != kind) {
            this.kind = kind;
            invalidate();
        }
    }

    @Override
    protected void onDraw(Canvas canvas) {
        float s = Math.min(getWidth(), getHeight()) * 0.36f;
        float cx = getWidth() / 2f, cy = getHeight() / 2f;
        path.reset();
        switch (kind) {
            case PLAY:
                path.moveTo(cx - s * 0.4f, cy - s * 0.6f);
                path.lineTo(cx + s * 0.6f, cy);
                path.lineTo(cx - s * 0.4f, cy + s * 0.6f);
                path.close();
                canvas.drawPath(path, paint);
                break;
            case PAUSE:
                canvas.drawRect(cx - s * 0.45f, cy - s * 0.55f, cx - s * 0.12f, cy + s * 0.55f, paint);
                canvas.drawRect(cx + s * 0.12f, cy - s * 0.55f, cx + s * 0.45f, cy + s * 0.55f, paint);
                break;
            default:
                // Next is a triangle and a bar on the right; previous mirrors it.
                float dir = kind == NEXT ? 1 : -1;
                path.moveTo(cx - dir * s * 0.5f, cy - s * 0.5f);
                path.lineTo(cx + dir * s * 0.3f, cy);
                path.lineTo(cx - dir * s * 0.5f, cy + s * 0.5f);
                path.close();
                canvas.drawPath(path, paint);
                float bx = cx + dir * s * 0.42f;
                canvas.drawRect(Math.min(bx, bx + dir * s * 0.16f), cy - s * 0.5f,
                        Math.max(bx, bx + dir * s * 0.16f), cy + s * 0.5f, paint);
        }
    }
}
