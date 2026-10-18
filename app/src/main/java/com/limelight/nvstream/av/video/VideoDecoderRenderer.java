package com.limelight.nvstream.av.video;

import com.limelight.nvstream.jni.MoonBridge;

public abstract class VideoDecoderRenderer {
    public abstract int setup(int format, int width, int height, int redrawRate);

    public abstract void start();

    public abstract void stop();

    // This is called once for each frame-start NALU. This means it will be called several times
    // for an IDR frame which contains several parameter sets and the I-frame data.
    public abstract int submitDecodeUnit(byte[] decodeUnitData, int decodeUnitLength, int decodeUnitType,
                                         int frameNumber, int frameType, char frameHostProcessingLatency,
                                         long receiveTimeUs, long enqueueTimeUs);

    // PyroWave frames are delivered whole with their packet map instead (two ints per RTP
    // packet: payload length, then MoonBridge.PYROWAVE_PACKET_* flags). Renderers without a
    // PyroWave decoder refuse them.
    public int submitPyroWaveFrame(byte[] frameData, int frameLength, int[] packets, int packetCount,
                                   int criticalPackets, int frameNumber, char frameHostProcessingLatency,
                                   long receiveTimeUs, long enqueueTimeUs) {
        return MoonBridge.DR_NEED_IDR;
    }

    public abstract void cleanup();

    public abstract int getCapabilities();

    public abstract void setHdrMode(boolean enabled, byte[] hdrMetadata);
}
