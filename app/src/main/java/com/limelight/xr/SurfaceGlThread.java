package com.limelight.xr;

import android.opengl.EGL14;
import android.opengl.EGLConfig;
import android.opengl.EGLContext;
import android.opengl.EGLDisplay;
import android.opengl.EGLExt;
import android.opengl.EGLSurface;
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

    private final Object lock = new Object();
    private final ArrayDeque<Runnable> events = new ArrayDeque<>();
    private boolean renderRequested;
    private boolean continuous;
    private boolean quit;

    private EGLDisplay eglDisplay = EGL14.EGL_NO_DISPLAY;
    private EGLContext eglContext = EGL14.EGL_NO_CONTEXT;
    private EGLSurface eglSurface = EGL14.EGL_NO_SURFACE;

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
                return;
            }
            renderer.onSurfaceCreated(null, null);
            renderer.onSurfaceChanged(null, width, height);

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
                    if (!EGL14.eglSwapBuffers(eglDisplay, eglSurface)) {
                        LimeLog.warning("eglSwapBuffers failed: 0x" + Integer.toHexString(EGL14.eglGetError()));
                        // The consumer is gone (entity disposed); stop drawing but keep draining events.
                        synchronized (lock) {
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

    private boolean initEgl() {
        eglDisplay = EGL14.eglGetDisplay(EGL14.EGL_DEFAULT_DISPLAY);
        if (eglDisplay == EGL14.EGL_NO_DISPLAY) {
            LimeLog.severe("eglGetDisplay failed");
            return false;
        }
        int[] version = new int[2];
        if (!EGL14.eglInitialize(eglDisplay, version, 0, version, 1)) {
            LimeLog.severe("eglInitialize failed: 0x" + Integer.toHexString(EGL14.eglGetError()));
            return false;
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
            LimeLog.severe("eglChooseConfig found no ES3 RGBA8 window config");
            return false;
        }
        int[] contextAttribs = {EGL14.EGL_CONTEXT_CLIENT_VERSION, 3, EGL14.EGL_NONE};
        eglContext = EGL14.eglCreateContext(eglDisplay, configs[0], EGL14.EGL_NO_CONTEXT, contextAttribs, 0);
        if (eglContext == EGL14.EGL_NO_CONTEXT) {
            LimeLog.severe("eglCreateContext failed: 0x" + Integer.toHexString(EGL14.eglGetError()));
            return false;
        }
        int[] surfaceAttribs = {EGL14.EGL_NONE};
        eglSurface = EGL14.eglCreateWindowSurface(eglDisplay, configs[0], surface, surfaceAttribs, 0);
        if (eglSurface == EGL14.EGL_NO_SURFACE) {
            LimeLog.severe("eglCreateWindowSurface failed: 0x" + Integer.toHexString(EGL14.eglGetError()));
            return false;
        }
        if (!EGL14.eglMakeCurrent(eglDisplay, eglSurface, eglSurface, eglContext)) {
            LimeLog.severe("eglMakeCurrent failed: 0x" + Integer.toHexString(EGL14.eglGetError()));
            return false;
        }
        LimeLog.info("XR stereo GL thread: ES3 context on a " + width + "x" + height + " surface");
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
