package com.limelight.binding.video;

import android.os.Build;
import android.view.Surface;

import com.limelight.LimeLog;
import com.limelight.nvstream.jni.MoonBridge;

import java.util.concurrent.locks.ReentrantReadWriteLock;

/**
 * Decodes and presents PyroWave streams with Vulkan compute (native pyrowave-renderer).
 *
 * PyroWave is intra-only, so there is no reference state to recover: a damaged frame is
 * dropped and the next one replaces it. Decode and present run synchronously on the
 * thread that submits the frame.
 */
public class PyroWaveDecoderRenderer {
    private static final int SUBMIT_ERROR = -1;
    // A submit error latches the native renderer (device lost, failed reset). After this
    // many consecutive errors the renderer is rebuilt once on the same surface; the same
    // run of errors again means the device is gone for good.
    static final int RECREATE_AFTER_ERRORS = 30;

    private static final boolean LIBRARY_LOADED = loadLibrary();

    // setup/cleanup own the native handle exclusively; submitFrame, setHdrMode and the
    // statistics getter share it. The HDR state is atomic on the native side, so the
    // control-stream thread's setHdrMode must not queue behind a submitFrame that can
    // block for seconds on a stalled surface: that same thread delivers rumble and
    // Steam haptic callbacks in order.
    private final ReentrantReadWriteLock handleLock = new ReentrantReadWriteLock();
    private volatile long handle;
    private volatile boolean lastFramePresented;

    // Setup arguments and the last HDR mode, so recreate() can rebuild the renderer.
    private Surface surface;
    private int width, height, frameRate;
    private boolean chroma444, tenBit;
    private boolean hdrEnabled;
    private float hdrPeakNits;

    // Touched by the submitting thread only.
    private int consecutiveSubmitErrors;
    private boolean recreated;
    private volatile boolean dead;

    private static boolean loadLibrary() {
        // Vulkan 1.3 loaders ship with newer Android releases; the native probe makes the
        // final decision, this only avoids loading the library where it can never work.
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.Q) {
            return false;
        }
        try {
            System.loadLibrary("pyrowave-renderer");
            return true;
        } catch (UnsatisfiedLinkError e) {
            // 32-bit builds do not include PyroWave.
            LimeLog.info("PyroWave renderer is not available: " + e.getMessage());
            return false;
        }
    }

    /**
     * Whether this device has a Vulkan 1.3 GPU with the features PyroWave needs.
     * The native probe runs once per process.
     */
    public static boolean isAvailable() {
        return LIBRARY_LOADED && nativeIsAvailable();
    }

    /**
     * @param tenBit a 10-bit profile was negotiated: the planes hold 10-bit code values and the
     *               host's HDR mode message decides between HDR10 (BT.2020 PQ) and 10-bit SDR
     */
    public boolean setup(Surface surface, int width, int height, int frameRate, boolean chroma444, boolean tenBit) {
        handleLock.writeLock().lock();
        try {
            cleanup();
            if (!LIBRARY_LOADED || surface == null || !surface.isValid()) {
                return false;
            }
            this.surface = surface;
            this.width = width;
            this.height = height;
            this.frameRate = frameRate;
            this.chroma444 = chroma444;
            this.tenBit = tenBit;
            consecutiveSubmitErrors = 0;
            recreated = false;
            dead = false;
            handle = nativeCreate(surface, width, height, frameRate, chroma444, tenBit);
            return handle != 0;
        } finally {
            handleLock.writeLock().unlock();
        }
    }

    /**
     * Rebuilds the native renderer on the surface given to setup(), keeping the HDR mode.
     * Called by the submitting thread after a run of submit errors; the surface itself is
     * still valid then (a destroyed surface goes through cleanup(), not here).
     */
    private boolean recreate() {
        handleLock.writeLock().lock();
        try {
            if (handle != 0) {
                nativeDestroy(handle);
                handle = 0;
            }
            if (surface == null || !surface.isValid()) {
                return false;
            }
            handle = nativeCreate(surface, width, height, frameRate, chroma444, tenBit);
            if (handle == 0) {
                return false;
            }
            nativeSetHdrMode(handle, hdrEnabled, hdrPeakNits);
            return true;
        } finally {
            handleLock.writeLock().unlock();
        }
    }

    /** The renderer failed repeatedly and could not be rebuilt; frames will never show again. */
    public boolean isDead() {
        return dead;
    }

    /**
     * The host's HDR mode for a 10-bit stream. On an HDR10-capable surface the picture is
     * presented as PQ; elsewhere the renderer tone-maps it to SDR using peakNits (MaxCLL or
     * the mastering display peak, 0 for the default).
     */
    public void setHdrMode(boolean enabled, float peakNits) {
        handleLock.readLock().lock();
        try {
            hdrEnabled = enabled;
            hdrPeakNits = peakNits;
            if (handle != 0) {
                nativeSetHdrMode(handle, enabled, peakNits);
            }
        } finally {
            handleLock.readLock().unlock();
        }
    }

    public int submitFrame(byte[] data, int length) {
        lastFramePresented = false;
        int result;
        handleLock.readLock().lock();
        try {
            if (handle == 0) {
                return MoonBridge.DR_NEED_IDR;
            }
            // Skipped frames are fine: every frame is a keyframe, so the next one recovers.
            // PyroWave has no IDR to request, so an error only asks for the next frame.
            result = nativeSubmitFrame(handle, data, length);
            lastFramePresented = result == 0;
        } finally {
            handleLock.readLock().unlock();
        }
        if (result != SUBMIT_ERROR) {
            consecutiveSubmitErrors = 0;
            return MoonBridge.DR_OK;
        }
        // Outside the read lock: recreate() takes the write lock.
        if (++consecutiveSubmitErrors == RECREATE_AFTER_ERRORS) {
            if (recreated) {
                LimeLog.severe("PyroWave renderer failed again after being rebuilt; giving up");
                dead = true;
            } else {
                recreated = true;
                LimeLog.warning("PyroWave renderer failed " + RECREATE_AFTER_ERRORS + " frames in a row; rebuilding it");
                if (recreate()) {
                    consecutiveSubmitErrors = 0;
                } else {
                    LimeLog.severe("PyroWave renderer could not be rebuilt");
                    dead = true;
                }
            }
        }
        return MoonBridge.DR_NEED_IDR;
    }

    /**
     * GPU time of the last completed decode in microseconds, or 0 when the GPU cannot report it.
     */
    public int getLastGpuDecodeUs() {
        handleLock.readLock().lock();
        try {
            return handle != 0 ? nativeGetLastGpuDecodeUs(handle) : 0;
        } finally {
            handleLock.readLock().unlock();
        }
    }

    public boolean wasLastFramePresented() {
        return lastFramePresented;
    }

    public void cleanup() {
        handleLock.writeLock().lock();
        try {
            if (handle != 0) {
                nativeDestroy(handle);
                handle = 0;
            }
        } finally {
            handleLock.writeLock().unlock();
        }
    }

    private static native boolean nativeIsAvailable();
    private static native long nativeCreate(Surface surface, int width, int height, int frameRate, boolean chroma444, boolean tenBit);
    private static native void nativeSetHdrMode(long handle, boolean enabled, float peakNits);
    private static native int nativeSubmitFrame(long handle, byte[] data, int length);
    private static native int nativeGetLastGpuDecodeUs(long handle);
    private static native void nativeDestroy(long handle);
}
