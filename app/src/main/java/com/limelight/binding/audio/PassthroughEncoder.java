package com.limelight.binding.audio;

import com.limelight.LimeLog;

/**
 * Streaming passthrough bitstream encoder (Dolby Digital, DTS, Dolby TrueHD)
 * backed by a minimal libavcodec (see app/src/main/jni/ptenc).
 *
 * Input is interleaved 16-bit PCM in Moonlight's channel order
 * (FL FR FC LFE BL BR for 5.1). Input chunks of any size are accepted;
 * output is produced whenever a complete write unit is available
 * (one AC-3 or DTS frame, or a batch of 16 TrueHD access units).
 *
 * Not thread-safe: use from the audio thread only.
 */
public class PassthroughEncoder implements PassthroughOutput.Encoder {

    private static final boolean LIBRARY_LOADED;

    static {
        boolean loaded;
        try {
            System.loadLibrary("moonlight-ptenc");
            loaded = true;
        } catch (Throwable t) {
            // The library is only built when the FFmpeg prebuilts are present
            LimeLog.warning("Passthrough encoder library unavailable: " + t.getMessage());
            loaded = false;
        }
        LIBRARY_LOADED = loaded;
    }

    /** The production encoder factory: the JNI encoders of this build. */
    public static final PassthroughOutput.EncoderFactory FACTORY = new PassthroughOutput.EncoderFactory() {
        @Override
        public boolean isAvailable() {
            return PassthroughEncoder.isAvailable();
        }

        @Override
        public boolean isSupported(PassthroughCodec codec) {
            return PassthroughEncoder.isSupported(codec);
        }

        @Override
        public PassthroughOutput.Encoder create(PassthroughCodec codec, int sampleRate, int channelCount,
                                                boolean iec61937) {
            return new PassthroughEncoder(codec, sampleRate, channelCount, 0, iec61937);
        }
    };

    private long handle;
    private final PassthroughCodec codec;
    private final int channelCount;
    private final int sampleRate;
    private final int writeSize;
    private final int codecDelay;
    private byte[] outBuffer = new byte[0];
    private long samplesIn;

    /** @return true if libmoonlight-ptenc.so is packaged in this build */
    public static boolean isAvailable() {
        return LIBRARY_LOADED;
    }

    /** @return true if this build can encode the given codec */
    public static boolean isSupported(PassthroughCodec codec) {
        return LIBRARY_LOADED && codec != null && nativeIsSupported(codec.id);
    }

    /**
     * @param sampleRate sample rate in Hz (48000 for Moonlight)
     * @param channelCount 2 or 6 (4 also works for AC-3 and DTS)
     * @param bitrate bits per second, or 0 for the codec default
     * @param iec61937 wrap each frame in an IEC 61937 burst for an ENCODING_IEC61937
     *                 track (AC-3 and DTS only)
     * @throws IllegalStateException if the encoder can't be created
     */
    public PassthroughEncoder(PassthroughCodec codec, int sampleRate, int channelCount, int bitrate, boolean iec61937) {
        if (!LIBRARY_LOADED) {
            throw new IllegalStateException("Passthrough encoder library not available");
        }

        handle = nativeCreate(codec.id, sampleRate, channelCount, bitrate, iec61937);
        if (handle == 0) {
            throw new IllegalStateException("Failed to create " + codec.displayName + " encoder ("
                    + sampleRate + " Hz, " + channelCount + " ch)");
        }

        this.codec = codec;
        this.channelCount = channelCount;
        this.sampleRate = sampleRate;
        this.writeSize = nativeWriteSize(handle);
        this.codecDelay = nativeCodecDelay(handle);
    }

    @Override
    public PassthroughCodec getCodec() {
        return codec;
    }

    /** @return samples per channel delivered by each write unit */
    @Override
    public int getWriteSize() {
        return writeSize;
    }

    /** @return decoder priming delay in samples per channel */
    @Override
    public int getCodecDelay() {
        return codecDelay;
    }

    /** @return samples per channel fed in but not yet returned as bitstream */
    @Override
    public long getBufferedSamples() {
        return handle != 0 ? samplesIn - nativeSamplesOutput(handle) : 0;
    }

    /** @return samples per channel represented by all bitstream returned so far */
    @Override
    public long getSamplesOutput() {
        return handle != 0 ? nativeSamplesOutput(handle) : 0;
    }

    public int getSampleRate() {
        return sampleRate;
    }

    /**
     * Feeds interleaved PCM. The returned byte count refers to {@link #getOutput()}
     * and holds zero or more complete write units.
     *
     * @param pcm interleaved samples; its length must be a multiple of the channel count
     * @return number of bytes available in getOutput(), or a negative value on error
     */
    @Override
    public int encode(short[] pcm) {
        if (handle == 0) {
            return -1;
        }
        int samplesPerChannel = pcm.length / channelCount;

        int maxOut = nativeMaxOutput(handle, samplesPerChannel);
        if (outBuffer.length < maxOut) {
            outBuffer = new byte[maxOut];
        }

        samplesIn += samplesPerChannel;
        return nativeEncode(handle, pcm, samplesPerChannel, outBuffer);
    }

    /** Buffer holding the output of the last {@link #encode(short[])} call. */
    @Override
    public byte[] getOutput() {
        return outBuffer;
    }

    @Override
    public void release() {
        if (handle != 0) {
            nativeDestroy(handle);
            handle = 0;
        }
    }

    private static native boolean nativeIsSupported(int codec);
    private static native long nativeCreate(int codec, int sampleRate, int channelCount, int bitrate, boolean iec61937);
    private static native int nativeWriteSize(long handle);
    private static native int nativeCodecDelay(long handle);
    private static native int nativeMaxOutput(long handle, int sampleCount);
    private static native long nativeSamplesOutput(long handle);
    private static native int nativeEncode(long handle, short[] pcm, int samplesPerChannel, byte[] out);
    private static native void nativeDestroy(long handle);
}
