package com.limelight.nvstream;

public interface NvConnectionListener {
    void stageStarting(String stage);
    void stageComplete(String stage);
    boolean stageFailed(String stage, int portFlags, int errorCode);
    
    void connectionStarted();
    void connectionTerminated(int errorCode);
    void connectionStatusUpdate(int connectionStatus);
    
    void displayMessage(String message);
    void displayTransientMessage(String message);

    void rumble(short controllerNumber, short lowFreqMotor, short highFreqMotor);
    void rumbleTriggers(short controllerNumber, short leftTrigger, short rightTrigger);

    void setHdrMode(boolean enabled, byte[] hdrMetadata);

    void setMotionEventState(short controllerNumber, byte motionType, short reportRateHz);

    void setControllerLED(short controllerNumber, byte r, byte g, byte b);

    /** A Steam Controller (2026) haptic output report to replay on the controller, id first. */
    void steamHaptic(short controllerNumber, byte[] report);

    /**
     * A Steam Controller (2026) settings write from the host's Steam: command 0x87 with
     * (id, lo, hi) triples, or 0x8E with none. The driver that owns the controller applies it.
     */
    void steamSettings(short controllerNumber, byte command, byte[] triples);
}
