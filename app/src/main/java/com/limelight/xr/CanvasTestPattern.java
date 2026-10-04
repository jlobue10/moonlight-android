package com.limelight.xr;

import android.graphics.Canvas;
import android.graphics.Color;
import android.graphics.Paint;
import android.view.Surface;

import com.limelight.LimeLog;

/**
 * Debug producer for the stereo SurfaceEntity: paints a test pattern with Canvas (the path the
 * AndroidX test app uses for still images) instead of OpenGL. If this shows on the headset while
 * the GL output does not, the entity works and the EGL producer is at fault; if even this stays
 * black, the entity itself (size, placement, stereo mode) is the problem.
 * Left half red, right half green, a white bar sweeping left to right, frame counter in the corner.
 */
public final class CanvasTestPattern extends Thread {
    private final Surface surface;
    private final int width;
    private final int height;
    private volatile boolean quit;

    public CanvasTestPattern(Surface surface, int width, int height) {
        super("XrCanvasTestPattern");
        this.surface = surface;
        this.width = width;
        this.height = height;
    }

    public void shutdown() {
        quit = true;
        interrupt();
        try {
            join(2000);
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
        }
    }

    @Override
    public void run() {
        Paint paint = new Paint();
        paint.setAntiAlias(false);
        Paint text = new Paint();
        text.setColor(Color.WHITE);
        text.setTextSize(Math.max(24, height / 12f));
        long frame = 0;
        LimeLog.info("XR canvas test pattern: starting on " + surface + " valid=" + surface.isValid());
        while (!quit) {
            Canvas c;
            try {
                c = surface.lockHardwareCanvas();
            } catch (RuntimeException e) {
                LimeLog.warning("XR canvas test pattern: lockHardwareCanvas failed: " + e);
                return;
            }
            if (c == null) {
                LimeLog.warning("XR canvas test pattern: no canvas");
                return;
            }
            try {
                paint.setColor(Color.rgb(200, 30, 30));
                c.drawRect(0, 0, c.getWidth() / 2f, c.getHeight(), paint);
                paint.setColor(Color.rgb(30, 200, 30));
                c.drawRect(c.getWidth() / 2f, 0, c.getWidth(), c.getHeight(), paint);
                float x = (frame % 100) / 100f * c.getWidth();
                paint.setColor(Color.WHITE);
                c.drawRect(x, 0, x + c.getWidth() / 50f, c.getHeight(), paint);
                c.drawText("XR test " + frame + "  canvas " + c.getWidth() + "x" + c.getHeight()
                        + "  requested " + width + "x" + height, 20, text.getTextSize() + 10, text);
            } finally {
                surface.unlockCanvasAndPost(c);
            }
            if (frame == 0 || frame % 50 == 0) {
                LimeLog.info("XR canvas test pattern: frame " + frame + " posted (" + c.getWidth() + "x" + c.getHeight() + ")");
            }
            frame++;
            try {
                Thread.sleep(100);
            } catch (InterruptedException e) {
                break;
            }
        }
        LimeLog.info("XR canvas test pattern: stopped after " + frame + " frames");
    }
}
