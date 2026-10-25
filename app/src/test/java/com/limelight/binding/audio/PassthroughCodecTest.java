package com.limelight.binding.audio;

import org.junit.Test;

import java.util.Arrays;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertNull;
import static org.junit.Assert.assertSame;
import static org.junit.Assert.assertTrue;

/** The settings-facing contract of the passthrough codec table; needs no Android runtime. */
public class PassthroughCodecTest {

    @Test
    public void preferenceValuesMapToCodecs() {
        assertSame(PassthroughCodec.AC3, PassthroughCodec.fromPrefValue("ac3"));
        assertSame(PassthroughCodec.DTS, PassthroughCodec.fromPrefValue("dts"));
        assertSame(PassthroughCodec.TRUEHD, PassthroughCodec.fromPrefValue("truehd"));
    }

    @Test
    public void offUnknownAndNullMeanPcm() {
        assertNull(PassthroughCodec.fromPrefValue("off"));
        assertNull(PassthroughCodec.fromPrefValue("eac3"));
        assertNull(PassthroughCodec.fromPrefValue(""));
        assertNull(PassthroughCodec.fromPrefValue(null));
    }

    @Test
    public void fallbackChainsDescendToDolbyDigital() {
        assertEquals(Arrays.asList(PassthroughCodec.TRUEHD, PassthroughCodec.DTS, PassthroughCodec.AC3),
                PassthroughCodec.fallbackChain(PassthroughCodec.TRUEHD));
        assertEquals(Arrays.asList(PassthroughCodec.DTS, PassthroughCodec.AC3),
                PassthroughCodec.fallbackChain(PassthroughCodec.DTS));
        assertEquals(Arrays.asList(PassthroughCodec.AC3),
                PassthroughCodec.fallbackChain(PassthroughCodec.AC3));
        assertTrue(PassthroughCodec.fallbackChain(null).isEmpty());
    }

    @Test
    public void nativeIdsMatchTheJniHeader() {
        // PtencCodec in app/src/main/jni/ptenc/ptenc.h: AC3 = 0, DTS = 1, TRUEHD = 2
        assertEquals(0, PassthroughCodec.AC3.id);
        assertEquals(1, PassthroughCodec.DTS.id);
        assertEquals(2, PassthroughCodec.TRUEHD.id);
    }

    @Test
    public void iecBurstsLastExactlyOneFrameAsStereoPcm() {
        // 1536 samples of 16-bit stereo = 6144 bytes; 512 samples = 2048 bytes
        assertEquals(6144, PassthroughCodec.AC3.iec61937BurstBytes());
        assertEquals(2048, PassthroughCodec.DTS.iec61937BurstBytes());
        assertTrue(PassthroughCodec.AC3.iec61937Capable);
        assertTrue(PassthroughCodec.DTS.iec61937Capable);
        assertFalse(PassthroughCodec.TRUEHD.iec61937Capable);
    }

    @Test
    public void writeUnitsMatchTheEncoderFraming() {
        assertEquals(1536, PassthroughCodec.AC3.writeUnitSamples);
        assertEquals(512, PassthroughCodec.DTS.writeUnitSamples);
        // 16 TrueHD access units of 40 samples per write
        assertEquals(640, PassthroughCodec.TRUEHD.writeUnitSamples);
    }
}
