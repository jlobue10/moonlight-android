package com.limelight.binding.video;

import android.os.Build;
import android.view.Surface;

import com.limelight.LimeLog;
import com.limelight.nvstream.jni.MoonBridge;

/**
 * Decodes and presents PyroWave streams with Vulkan compute (native pyrowave-renderer).
 *
 * PyroWave is intra-only, so there is no reference state to recover: a damaged frame is
 * dropped and the next one replaces it. Decode and present run synchronously on the
 * thread that submits the frame.
 */
public class PyroWaveDecoderRenderer {
    private static final int SUBMIT_ERROR = -1;

    private static final boolean LIBRARY_LOADED = loadLibrary();

    private long handle;
    private boolean lastFramePresented;

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
    public synchronized boolean setup(Surface surface, int width, int height, int frameRate, boolean chroma444, boolean tenBit) {
        cleanup();
        if (!LIBRARY_LOADED || surface == null || !surface.isValid()) {
            return false;
        }
        handle = nativeCreate(surface, width, height, frameRate, chroma444, tenBit);
        return handle != 0;
    }

    /**
     * The host's HDR mode for a 10-bit stream. On an HDR10-capable surface the picture is
     * presented as PQ; elsewhere the renderer tone-maps it to SDR using peakNits (MaxCLL or
     * the mastering display peak, 0 for the default).
     */
    public synchronized void setHdrMode(boolean enabled, float peakNits) {
        if (handle != 0) {
            nativeSetHdrMode(handle, enabled, peakNits);
        }
    }

    public synchronized int submitFrame(byte[] data, int length) {
        lastFramePresented = false;
        if (handle == 0) {
            return MoonBridge.DR_NEED_IDR;
        }
        // Skipped frames are fine: every frame is a keyframe, so the next one recovers.
        // PyroWave has no IDR to request, so an error only asks for the next frame.
        int result = nativeSubmitFrame(handle, data, length);
        lastFramePresented = result == 0;
        return result == SUBMIT_ERROR ? MoonBridge.DR_NEED_IDR : MoonBridge.DR_OK;
    }

    /**
     * GPU time of the last completed decode in microseconds, or 0 when the GPU cannot report it.
     */
    public synchronized int getLastGpuDecodeUs() {
        return handle != 0 ? nativeGetLastGpuDecodeUs(handle) : 0;
    }

    public synchronized boolean wasLastFramePresented() {
        return lastFramePresented;
    }

    public synchronized void cleanup() {
        if (handle != 0) {
            nativeDestroy(handle);
            handle = 0;
        }
    }

    private static native boolean nativeIsAvailable();
    private static native long nativeCreate(Surface surface, int width, int height, int frameRate, boolean chroma444, boolean tenBit);
    private static native void nativeSetHdrMode(long handle, boolean enabled, float peakNits);
    private static native int nativeSubmitFrame(long handle, byte[] data, int length);
    private static native int nativeGetLastGpuDecodeUs(long handle);
    private static native void nativeDestroy(long handle);
}
