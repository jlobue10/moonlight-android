package com.limelight.xr;

import android.opengl.EGL14;
import android.opengl.EGLConfig;
import android.opengl.EGLContext;
import android.opengl.EGLDisplay;
import android.opengl.EGLExt;
import android.opengl.EGLSurface;
import android.opengl.GLES20;
import android.opengl.GLSurfaceView;
import android.view.Surface;

import com.limelight.LimeLog;
import com.limelight.utils.Stereo3DRenderer;

import java.util.ArrayDeque;

/**
 * Drives a {@link GLSurfaceView.Renderer} on its own EGL context against an arbitrary
 * {@link Surface}, the way GLSurfaceView does for its own window. Used to render the side-by-side
 * 3D output into the Surface of an Android XR {@code SurfaceEntity}, which is not a View.
 *
 * Semantics mirror GLSurfaceView: {@link #requestRender()} draws one frame,
 * {@link #setContinuousRendering(boolean)} switches between RENDERMODE_WHEN_DIRTY and
 * RENDERMODE_CONTINUOUSLY, and {@link #queueEvent(Runnable)} runs work on the GL thread. Events
 * queued before {@link #shutdown()} (the renderer's GL cleanup) still run before the context goes.
 */
public final class SurfaceGlThread extends Thread implements Stereo3DRenderer.RenderHost {

    private final Surface surface;
    private final int width;
    private final int height;
    private GLSurfaceView.Renderer renderer;
    private InitListener initListener;
    private String lastError;

    /** Told once if the thread could not bring up EGL or the renderer; the caller should fall back. */
    public interface InitListener {
        void onInitFailed(String reason);
    }

    public void setInitListener(InitListener listener) {
        this.initListener = listener;
    }

    private final Object lock = new Object();
    private final ArrayDeque<Runnable> events = new ArrayDeque<>();
    private boolean renderRequested;
    private boolean continuous;
    // eglSwapBuffers failed: the consumer is gone, so draw requests are dropped.
    private boolean swapFailed;
    private boolean quit;

    private EGLDisplay eglDisplay = EGL14.EGL_NO_DISPLAY;
    private EGLContext eglContext = EGL14.EGL_NO_CONTEXT;
    private EGLSurface eglSurface = EGL14.EGL_NO_SURFACE;
    private long swaps;
    private long lastReportNs;
    private final java.nio.ByteBuffer probe = java.nio.ByteBuffer.allocateDirect(4);

    public SurfaceGlThread(Surface surface, int width, int height) {
        super("XrStereoGl");
        this.surface = surface;
        this.width = width;
        this.height = height;
    }

    /** Must be called before {@link #start()}. */
    public void setRenderer(GLSurfaceView.Renderer renderer) {
        this.renderer = renderer;
    }

    // --- RenderHost ---

    @Override
    public void requestRender() {
        synchronized (lock) {
            renderRequested = true;
            lock.notifyAll();
        }
    }

    @Override
    public void setContinuousRendering(boolean continuous) {
        synchronized (lock) {
            this.continuous = continuous;
            lock.notifyAll();
        }
    }

    @Override
    public void queueEvent(Runnable r) {
        synchronized (lock) {
            events.add(r);
            lock.notifyAll();
        }
    }

    @Override
    public int getWidth() {
        return width;
    }

    /**
     * fork.10 flipped the eye pass on the assumption (from a CustomMesh project) that the compositor
     * samples GL buffers top-down; on a Quad SurfaceEntity the picture then showed upside down
     * (Galaxy XR, fork.12). A quad is sampled like a window surface, so no flip.
     */
    @Override
    public boolean flipOutputVertically() {
        return false;
    }

    @Override
    public int getHeight() {
        return height;
    }

    /** Stops the loop after draining queued events and releases EGL. Blocks briefly. */
    public void shutdown() {
        synchronized (lock) {
            quit = true;
            lock.notifyAll();
        }
        try {
            join(3000);
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
        }
    }

    @Override
    public void run() {
        if (renderer == null) {
            LimeLog.severe("SurfaceGlThread started without a renderer");
            return;
        }
        try {
            if (!initEgl()) {
                notifyInitFailed(lastError != null ? lastError : "EGL setup failed");
                return;
            }
            try {
                renderer.onSurfaceCreated(null, null);
                renderer.onSurfaceChanged(null, width, height);
            } catch (RuntimeException e) {
                LimeLog.severe("XR stereo renderer failed to start: " + e);
                notifyInitFailed("renderer start failed: " + e.getMessage());
                return;
            }

            while (true) {
                Runnable event;
                boolean draw;
                synchronized (lock) {
                    while (!quit && events.isEmpty() && !renderRequested && !continuous) {
                        lock.wait();
                    }
                    if (quit && events.isEmpty()) {
                        break;
                    }
                    event = events.poll();
                    if (swapFailed) {
                        // Every decoded frame still requests a draw; do not redraw a full
                        // stereo frame into a dead surface for each of them.
                        renderRequested = false;
                        continuous = false;
                    }
                    draw = event == null && !quit && (renderRequested || continuous);
                    if (draw) {
                        renderRequested = false;
                    }
                }
                if (event != null) {
                    event.run();
                    continue;
                }
                if (draw) {
                    renderer.onDrawFrame(null);
                    reportProgress();
                    if (!EGL14.eglSwapBuffers(eglDisplay, eglSurface)) {
                        LimeLog.warning("eglSwapBuffers failed: 0x" + Integer.toHexString(EGL14.eglGetError()));
                        // The consumer is gone (entity disposed); stop drawing but keep draining events.
                        synchronized (lock) {
                            swapFailed = true;
                            continuous = false;
                            renderRequested = false;
                        }
                    }
                }
            }
        } catch (InterruptedException ignored) {
            // shutting down
        } catch (RuntimeException e) {
            LimeLog.severe("XR stereo GL thread died: " + e);
        } finally {
            releaseEgl();
        }
    }

    private void notifyInitFailed(String reason) {
        InitListener l = initListener;
        if (l != null) {
            l.onInitFailed(reason);
        }
    }

    private boolean fail(String message) {
        lastError = message;
        LimeLog.severe(message);
        return false;
    }

    /**
     * Diagnostics for the headset (no adb there): about every 5 s, log how many frames were swapped,
     * the GL error state and the colour of the pixel at the centre of the left eye *as rendered*.
     * A non-black pixel here with a black screen means the entity is not showing our buffers; a
     * black pixel means our own render path is at fault.
     */
    private void reportProgress() {
        swaps++;
        long now = System.nanoTime();
        if (swaps != 1 && now - lastReportNs < 5_000_000_000L) {
            return;
        }
        lastReportNs = now;
        if (!Boolean.TRUE.equals(Stereo3DRenderer.isDebugMode)) {
            LimeLog.info("XR stereo GL: " + swaps + " frames swapped");
            return;
        }
        // Readback can force GPU completion. Keep pixel diagnostics opt-in.
        probe.clear();
        GLES20.glReadPixels(width / 4, height / 2, 1, 1, GLES20.GL_RGBA, GLES20.GL_UNSIGNED_BYTE, probe);
        int err = GLES20.glGetError();
        LimeLog.info("XR stereo GL: " + swaps + " frames swapped, centre-left pixel rgba=("
                + (probe.get(0) & 0xFF) + "," + (probe.get(1) & 0xFF) + "," + (probe.get(2) & 0xFF) + ","
                + (probe.get(3) & 0xFF) + ")" + (err != GLES20.GL_NO_ERROR ? ", glGetError=0x" + Integer.toHexString(err) : ""));
    }

    private boolean initEgl() {
        eglDisplay = EGL14.eglGetDisplay(EGL14.EGL_DEFAULT_DISPLAY);
        if (eglDisplay == EGL14.EGL_NO_DISPLAY) {
            return fail("eglGetDisplay failed");
        }
        int[] version = new int[2];
        if (!EGL14.eglInitialize(eglDisplay, version, 0, version, 1)) {
            return fail("eglInitialize failed: 0x" + Integer.toHexString(EGL14.eglGetError()));
        }
        int[] configAttribs = {
                EGL14.EGL_RED_SIZE, 8,
                EGL14.EGL_GREEN_SIZE, 8,
                EGL14.EGL_BLUE_SIZE, 8,
                EGL14.EGL_ALPHA_SIZE, 8,
                EGL14.EGL_RENDERABLE_TYPE, EGLExt.EGL_OPENGL_ES3_BIT_KHR,
                EGL14.EGL_SURFACE_TYPE, EGL14.EGL_WINDOW_BIT,
                EGL14.EGL_NONE
        };
        EGLConfig[] configs = new EGLConfig[1];
        int[] numConfigs = new int[1];
        if (!EGL14.eglChooseConfig(eglDisplay, configAttribs, 0, configs, 0, 1, numConfigs, 0) || numConfigs[0] == 0) {
            return fail("eglChooseConfig found no ES3 RGBA8 window config");
        }
        int[] contextAttribs = {EGL14.EGL_CONTEXT_CLIENT_VERSION, 3, EGL14.EGL_NONE};
        eglContext = EGL14.eglCreateContext(eglDisplay, configs[0], EGL14.EGL_NO_CONTEXT, contextAttribs, 0);
        if (eglContext == EGL14.EGL_NO_CONTEXT) {
            return fail("eglCreateContext failed: 0x" + Integer.toHexString(EGL14.eglGetError()));
        }
        int[] surfaceAttribs = {EGL14.EGL_NONE};
        eglSurface = EGL14.eglCreateWindowSurface(eglDisplay, configs[0], surface, surfaceAttribs, 0);
        if (eglSurface == EGL14.EGL_NO_SURFACE) {
            return fail("eglCreateWindowSurface failed: 0x" + Integer.toHexString(EGL14.eglGetError()));
        }
        if (!EGL14.eglMakeCurrent(eglDisplay, eglSurface, eglSurface, eglContext)) {
            return fail("eglMakeCurrent failed: 0x" + Integer.toHexString(EGL14.eglGetError()));
        }
        // Opaque black for anything the renderer leaves uncovered; the entity is told it is opaque.
        GLES20.glClearColor(0f, 0f, 0f, 1f);
        int[] w = new int[1], h = new int[1];
        EGL14.eglQuerySurface(eglDisplay, eglSurface, EGL14.EGL_WIDTH, w, 0);
        EGL14.eglQuerySurface(eglDisplay, eglSurface, EGL14.EGL_HEIGHT, h, 0);
        LimeLog.info("XR stereo GL thread: ES3 context; requested " + width + "x" + height
                + ", EGL reports " + w[0] + "x" + h[0] + " on " + surface);
        return true;
    }

    private void releaseEgl() {
        if (eglDisplay != EGL14.EGL_NO_DISPLAY) {
            EGL14.eglMakeCurrent(eglDisplay, EGL14.EGL_NO_SURFACE, EGL14.EGL_NO_SURFACE, EGL14.EGL_NO_CONTEXT);
            if (eglSurface != EGL14.EGL_NO_SURFACE) {
                EGL14.eglDestroySurface(eglDisplay, eglSurface);
            }
            if (eglContext != EGL14.EGL_NO_CONTEXT) {
                EGL14.eglDestroyContext(eglDisplay, eglContext);
            }
            EGL14.eglTerminate(eglDisplay);
        }
        eglSurface = EGL14.EGL_NO_SURFACE;
        eglContext = EGL14.EGL_NO_CONTEXT;
        eglDisplay = EGL14.EGL_NO_DISPLAY;
    }
}
