package com.limelight.binding.audio;

import java.util.ArrayList;
import java.util.List;

/**
 * Bitstream formats the surround passthrough can encode 5.1 PCM into.
 *
 * Pure Java on purpose: the settings code, the audio renderer and the host
 * regression harnesses all use it without loading the JNI encoder.
 */
public enum PassthroughCodec {
    // id must match PtencCodec in app/src/main/jni/ptenc/ptenc.h
    AC3(0, "ac3", "Dolby Digital", 1536, 2560, true),
    DTS(1, "dts", "DTS", 512, 2012, true),
    // 16 access units of 40 samples per write; variable bitrate, ~6 Mbps peak
    TRUEHD(2, "truehd", "Dolby TrueHD", 40 * 16, 10240, false);

    public final int id;
    public final String prefValue;
    public final String displayName;
    /** Samples per channel represented by one write unit (one frame, or a TrueHD batch). */
    public final int writeUnitSamples;
    /** Nominal size of one write unit in bytes at the default bitrate, used to size the AudioTrack buffer. */
    public final int writeUnitBytes;
    /** Whether a frame can be wrapped in an IEC 61937 burst and played through an ENCODING_IEC61937 track. */
    public final boolean iec61937Capable;

    PassthroughCodec(int id, String prefValue, String displayName, int writeUnitSamples, int writeUnitBytes,
                     boolean iec61937Capable) {
        this.id = id;
        this.prefValue = prefValue;
        this.displayName = displayName;
        this.writeUnitSamples = writeUnitSamples;
        this.writeUnitBytes = writeUnitBytes;
        this.iec61937Capable = iec61937Capable;
    }

    /** Size of one IEC 61937 burst for this codec: the frame's duration as 16-bit stereo PCM. */
    public int iec61937BurstBytes() {
        return writeUnitSamples * 4;
    }

    /** @return the codec for a preference value, or null for "off", null or an unknown value */
    public static PassthroughCodec fromPrefValue(String value) {
        if (value == null) {
            return null;
        }
        for (PassthroughCodec codec : values()) {
            if (codec.prefValue.equals(value)) {
                return codec;
            }
        }
        return null;
    }

    /**
     * The requested codec followed by the cheaper formats to fall back to when the
     * build lacks the encoder or the output refuses the format: TrueHD, then DTS, then AC-3.
     */
    public static List<PassthroughCodec> fallbackChain(PassthroughCodec requested) {
        List<PassthroughCodec> chain = new ArrayList<>();
        if (requested == null) {
            return chain;
        }
        switch (requested) {
            case TRUEHD:
                chain.add(TRUEHD);
                // fall through
            case DTS:
                chain.add(DTS);
                // fall through
            case AC3:
                chain.add(AC3);
                break;
        }
        return chain;
    }
}
