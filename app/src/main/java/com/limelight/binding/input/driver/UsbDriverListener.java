package com.limelight.binding.input.driver;

public interface UsbDriverListener {
    void reportControllerState(int controllerId, int buttonFlags,
                               float leftStickX, float leftStickY,
                               float rightStickX, float rightStickY,
                               float leftTrigger, float rightTrigger);
    void reportControllerMotion(int controllerId, byte motionType, float motionX, float motionY, float motionZ);
    /** Touchpad event from a controller with one or two pads (touchpadIndex 0/1); coordinates 0..1. */
    default void reportControllerTouch(int controllerId, byte touchpadIndex, byte eventType, int pointerId,
                                       float x, float y, float pressure) {}
    /** Battery state (MoonBridge.LI_BATTERY_STATE_*) and percentage 0..100. */
    default void reportControllerBattery(int controllerId, byte batteryState, byte batteryPercentage) {}

    void deviceRemoved(AbstractController controller);
    void deviceAdded(AbstractController controller);
}
