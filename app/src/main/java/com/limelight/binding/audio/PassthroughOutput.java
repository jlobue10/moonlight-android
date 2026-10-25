package com.limelight.binding.audio;

import android.media.AudioAttributes;
import android.media.AudioFormat;
import android.media.AudioManager;
import android.media.AudioTrack;
import android.os.Build;

import com.limelight.LimeLog;

/**
 * A compressed (bitstream) audio output: an encoder that turns 5.1 PCM into
 * Dolby Digital, DTS or Dolby TrueHD frames, plus the AudioTrack configuration
 * that the device accepted for that bitstream.
 *
 * {@link #open} walks the fallback chain of the requested codec and, for each
 * codec the build can encode, tries the output formats a TV or receiver may
 * accept, most compatible first:
 * <ol>
 *   <li>IEC 61937 bursts written to an {@code ENCODING_IEC61937} stereo track. Every
 *       burst lasts exactly one frame, so the framework paces the stream like PCM.
 *       With plain compressed encodings some HALs drain the bitstream faster than
 *       real time and underrun constantly.</li>
 *   <li>The codec's own encoding ({@code ENCODING_AC3}, ...) declared as 5.1, then as
 *       stereo, since some HALs only accept compressed formats declared as stereo.</li>
 * </ol>
 * The AudioTrack itself is owned by the renderer; this class only remembers how to
 * build an identical one when the output dies (HDMI/eARC renegotiation).
 */
public final class PassthroughOutput {

    /** One codec's streaming encoder. Fed PCM, returns complete write units. */
    public interface Encoder {
        PassthroughCodec getCodec();

        /** Samples per channel represented by one write unit. */
        int getWriteSize();

        /** Decoder priming delay in samples per channel. */
        int getCodecDelay();

        /** Samples per channel fed in but not yet returned as bitstream. */
        long getBufferedSamples();

        /** Samples per channel represented by all bitstream returned so far. */
        long getSamplesOutput();

        /**
         * Feeds interleaved PCM.
         *
         * @return bytes available in {@link #getOutput()} (zero or more complete write units),
         *         or a negative value on error
         */
        int encode(short[] pcm);

        /** Buffer holding the output of the last {@link #encode(short[])} call. */
        byte[] getOutput();

        void release();
    }

    /** Where encoders come from: the JNI library in production, a fake in the regression harness. */
    public interface EncoderFactory {
        /** @return whether any encoder is packaged in this build */
        boolean isAvailable();

        boolean isSupported(PassthroughCodec codec);

        /** @throws IllegalStateException if the encoder cannot be created */
        Encoder create(PassthroughCodec codec, int sampleRate, int channelCount, boolean iec61937);
    }

    private final PassthroughCodec codec;
    private final boolean iec61937;
    private final AudioFormat format;
    private final AudioAttributes attributes;
    private final int channelMask;
    private final int bufferSize;
    private final int unitBytes;
    private Encoder encoder;
    private AudioTrack initialTrack;

    private PassthroughOutput(PassthroughCodec codec, boolean iec61937, AudioFormat format,
                              AudioAttributes attributes, int channelMask, int bufferSize, int unitBytes,
                              Encoder encoder, AudioTrack initialTrack) {
        this.codec = codec;
        this.iec61937 = iec61937;
        this.format = format;
        this.attributes = attributes;
        this.channelMask = channelMask;
        this.bufferSize = bufferSize;
        this.unitBytes = unitBytes;
        this.encoder = encoder;
        this.initialTrack = initialTrack;
    }

    /**
     * Tries to open a bitstream output for the requested codec or one of its fallbacks.
     *
     * @param bufferFrames AudioTrack buffer size in write units, or 0 for the platform minimum
     *                     (at least two units)
     * @return the opened output, or null when nothing could be opened and PCM must be used
     */
    public static PassthroughOutput open(PassthroughCodec requested, int bufferFrames, int sampleRate,
                                         int channelCount, EncoderFactory encoders) {
        if (requested == null || encoders == null) {
            return null;
        }
        if (!encoders.isAvailable()) {
            LimeLog.warning("Passthrough: encoder library not included in this build");
            return null;
        }

        // Same attributes as video players use, which is what TV audio HALs expect
        // to route to a direct/passthrough HDMI output.
        AudioAttributes attributes = new AudioAttributes.Builder()
                .setUsage(AudioAttributes.USAGE_MEDIA)
                .setContentType(AudioAttributes.CONTENT_TYPE_MOVIE)
                .build();

        for (PassthroughCodec codec : PassthroughCodec.fallbackChain(requested)) {
            if (!encoders.isSupported(codec)) {
                LimeLog.info("Passthrough: " + codec.displayName + " not available in this build");
                continue;
            }

            if (codec.iec61937Capable && Build.VERSION.SDK_INT >= Build.VERSION_CODES.N) {
                PassthroughOutput output = tryOpen(codec, true, AudioFormat.ENCODING_IEC61937,
                        AudioFormat.CHANNEL_OUT_STEREO, codec.iec61937BurstBytes(), bufferFrames,
                        sampleRate, channelCount, attributes, encoders);
                if (output != null) {
                    return finish(output, requested);
                }
            }

            int encoding = getEncoding(codec);
            if (encoding < 0) {
                LimeLog.info("Passthrough: " + codec.displayName + " not supported on this Android version");
                continue;
            }

            int[] channelMasks = { AudioFormat.CHANNEL_OUT_5POINT1, AudioFormat.CHANNEL_OUT_STEREO };
            for (int channelMask : channelMasks) {
                PassthroughOutput output = tryOpen(codec, false, encoding, channelMask, codec.writeUnitBytes,
                        bufferFrames, sampleRate, channelCount, attributes, encoders);
                if (output != null) {
                    return finish(output, requested);
                }
            }
        }

        return null;
    }

    private static PassthroughOutput finish(PassthroughOutput output, PassthroughCodec requested) {
        if (output.codec != requested) {
            LimeLog.warning("Passthrough: fell back from " + requested.displayName + " to " + output.codec.displayName);
        }
        return output;
    }

    // AudioFormat encoding for a codec, or -1 if this Android version can't pass it through
    private static int getEncoding(PassthroughCodec codec) {
        switch (codec) {
            case AC3:
                return AudioFormat.ENCODING_AC3;
            case DTS:
                return AudioFormat.ENCODING_DTS;
            case TRUEHD:
                return Build.VERSION.SDK_INT >= Build.VERSION_CODES.N_MR1 ?
                        AudioFormat.ENCODING_DOLBY_TRUEHD : -1;
            default:
                return -1;
        }
    }

    private static PassthroughOutput tryOpen(PassthroughCodec codec, boolean iec61937, int encoding, int channelMask,
                                             int unitBytes, int bufferFrames, int sampleRate, int channelCount,
                                             AudioAttributes attributes, EncoderFactory encoders) {
        String mode = codec.displayName + (iec61937 ? " (IEC 61937)" : "");
        AudioFormat format = new AudioFormat.Builder()
                .setEncoding(encoding)
                .setSampleRate(sampleRate)
                .setChannelMask(channelMask)
                .build();

        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q &&
                !AudioTrack.isDirectPlaybackSupported(format, attributes)) {
            LimeLog.info(String.format("Passthrough: %s direct playback not supported with channel mask 0x%X",
                    mode, channelMask));
            return null;
        }

        // AudioFlinger won't start a streaming track until its buffer is full,
        // so the buffer size is effectively our output latency. Try the requested
        // size first, then something larger.
        int minBufferSize = Math.max(0, AudioTrack.getMinBufferSize(sampleRate, channelMask, encoding));
        int requested = bufferFrames > 0 ?
                unitBytes * bufferFrames : Math.max(minBufferSize, unitBytes * 2);
        if (iec61937) {
            // Whole bursts only
            requested = (requested + unitBytes - 1) / unitBytes * unitBytes;
        }
        int larger = Math.max(minBufferSize, requested + unitBytes * 2);
        int[] bufferSizes = larger != requested ? new int[] { requested, larger } : new int[] { requested };

        for (int bufferSize : bufferSizes) {
            AudioTrack track = null;
            Encoder encoder = null;
            try {
                track = createTrack(format, attributes, bufferSize);
                if (track.getState() != AudioTrack.STATE_INITIALIZED) {
                    throw new IllegalStateException("AudioTrack not initialized");
                }

                encoder = encoders.create(codec, sampleRate, channelCount, iec61937);
                track.play();

                LimeLog.info(String.format("Passthrough enabled: %s, mask 0x%X, buffer %d bytes = %.1f units (setting: %s, platform minimum %d bytes)",
                        mode, channelMask, bufferSize, (double) bufferSize / unitBytes,
                        bufferFrames > 0 ? bufferFrames + " units" : "auto", minBufferSize));
                return new PassthroughOutput(codec, iec61937, format, attributes, channelMask, bufferSize, unitBytes,
                        encoder, track);
            } catch (Exception e) {
                LimeLog.warning(String.format("Passthrough: %s mask 0x%X buffer %d failed: %s",
                        mode, channelMask, bufferSize, e));
                if (track != null) {
                    try {
                        track.release();
                    } catch (Exception ignored) {}
                }
                if (encoder != null) {
                    encoder.release();
                }
            }
        }
        return null;
    }

    private static AudioTrack createTrack(AudioFormat format, AudioAttributes attributes, int bufferSize) {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            // No low latency performance mode here: it's only available for PCM
            return new AudioTrack.Builder()
                    .setAudioFormat(format)
                    .setAudioAttributes(attributes)
                    .setTransferMode(AudioTrack.MODE_STREAM)
                    .setBufferSizeInBytes(bufferSize)
                    .build();
        }
        else {
            return new AudioTrack(attributes,
                    format,
                    bufferSize,
                    AudioTrack.MODE_STREAM,
                    AudioManager.AUDIO_SESSION_ID_GENERATE);
        }
    }

    public PassthroughCodec getCodec() {
        return codec;
    }

    public boolean isIec61937() {
        return iec61937;
    }

    public int getBufferSize() {
        return bufferSize;
    }

    public int getUnitBytes() {
        return unitBytes;
    }

    public Encoder getEncoder() {
        return encoder;
    }

    /** The track opened by {@link #open}, handed over exactly once; null afterwards. */
    public AudioTrack takeInitialTrack() {
        AudioTrack track = initialTrack;
        initialTrack = null;
        return track;
    }

    /**
     * Builds and starts a track identical to the one {@link #open} succeeded with, for use after
     * the previous one died.
     *
     * @throws IllegalStateException if the track cannot be created or started
     */
    public AudioTrack createTrack() {
        AudioTrack track = createTrack(format, attributes, bufferSize);
        try {
            if (track.getState() != AudioTrack.STATE_INITIALIZED) {
                throw new IllegalStateException("AudioTrack not initialized");
            }
            track.play();
        } catch (RuntimeException e) {
            try {
                track.release();
            } catch (Exception ignored) {}
            throw e;
        }
        return track;
    }

    public void releaseEncoder() {
        if (encoder != null) {
            encoder.release();
            encoder = null;
        }
        if (initialTrack != null) {
            try {
                initialTrack.release();
            } catch (Exception ignored) {}
            initialTrack = null;
        }
    }

    public String describe() {
        return String.format("%s%s, mask 0x%X, buffer %d bytes", codec.displayName,
                iec61937 ? " IEC61937" : "", channelMask, bufferSize);
    }
}
