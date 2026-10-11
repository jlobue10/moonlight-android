package com.limelight.binding.input.driver;

import android.content.Context;
import android.content.SharedPreferences;
import android.os.SystemClock;
import com.limelight.LimeLog;
import com.limelight.nvstream.input.ControllerPacket;
import com.limelight.nvstream.jni.MoonBridge;
import java.util.Locale;

/** Shared SC2026 input mapping for the BLE and USB/puck transports. */
public abstract class SteamControllerInput extends AbstractController {
    public static final int GRIPS_PADDLES = 0;
    public static final int GRIPS_DS4 = 1;
    public static final int GRIPS_OFF = 2;
    protected final boolean motionEnabled;
    protected final boolean splitPads;
    private final int gripsMode;
    private final boolean stickRim;
    private final Context inputContext;
    private final String calibrationKey;
    private final String transportName;

    // Button bit indices inside the 32-bit field at report bytes 2..5 (bit = (byte - 2) * 8 + n)
    private static final int BTN_A = 0, BTN_B = 1, BTN_X = 2, BTN_Y = 3, BTN_QUICK_ACCESS = 4,
            BTN_RSTICK_CLICK = 5, BTN_MENU = 6, BTN_GRIP_R_TOP = 7,
            BTN_GRIP_R_BOTTOM = 8, BTN_RB = 9, BTN_DPAD_DOWN = 10, BTN_DPAD_RIGHT = 11,
            BTN_DPAD_LEFT = 12, BTN_DPAD_UP = 13, BTN_VIEW = 14, BTN_LSTICK_CLICK = 15,
            BTN_STEAM = 16, BTN_GRIP_L_TOP = 17, BTN_GRIP_L_BOTTOM = 18, BTN_LB = 19,
            BTN_RSTICK_TOUCH = 20, BTN_RPAD_TOUCH = 21, BTN_RPAD_CLICK = 22, BTN_RT_FULL = 23,
            BTN_LSTICK_TOUCH = 24, BTN_LPAD_TOUCH = 25, BTN_LPAD_CLICK = 26, BTN_LT_FULL = 27,
            BTN_GRIP_R_TOUCH = 28, BTN_GRIP_L_TOUCH = 29;   // capacitive grip sensors

    private static final int SUPPORTED_BUTTONS =
            ControllerPacket.A_FLAG | ControllerPacket.B_FLAG | ControllerPacket.X_FLAG | ControllerPacket.Y_FLAG |
            ControllerPacket.UP_FLAG | ControllerPacket.DOWN_FLAG | ControllerPacket.LEFT_FLAG | ControllerPacket.RIGHT_FLAG |
            ControllerPacket.LB_FLAG | ControllerPacket.RB_FLAG | ControllerPacket.PLAY_FLAG | ControllerPacket.BACK_FLAG |
            ControllerPacket.LS_CLK_FLAG | ControllerPacket.RS_CLK_FLAG | ControllerPacket.SPECIAL_BUTTON_FLAG |
            ControllerPacket.PADDLE1_FLAG | ControllerPacket.PADDLE2_FLAG | ControllerPacket.PADDLE3_FLAG | ControllerPacket.PADDLE4_FLAG |
            ControllerPacket.TOUCHPAD_FLAG | ControllerPacket.MISC_FLAG |
            ControllerPacket.LEFT_GRIP_TOUCH_FLAG | ControllerPacket.RIGHT_GRIP_TOUCH_FLAG |
            ControllerPacket.LEFT_STICK_TOUCH_FLAG | ControllerPacket.RIGHT_STICK_TOUCH_FLAG |
            ControllerPacket.LEFT_PAD_CLICK_FLAG | ControllerPacket.RIGHT_PAD_CLICK_FLAG |
            ControllerPacket.LEFT_TRIGGER_CLICK_FLAG | ControllerPacket.RIGHT_TRIGGER_CLICK_FLAG;

    // Last state handed to the listener (handleState dedup); invalid until the first report
    // after each announcement so a reconnected controller always resends its state.
    protected boolean reportedStateValid;
    private int reportedButtonFlags;
    private float reportedLeftTrigger, reportedRightTrigger;
    private float reportedLeftStickX, reportedLeftStickY, reportedRightStickX, reportedRightStickY;

    // Stick extent diagnostics (stream log, every 10 s while a stick is away from centre).
    // All of it is measured on the raw controller values, before the rim calibration below.
    private float stickLogLeftMax, stickLogRightMax;
    private float stickLogLeftAxisX, stickLogLeftAxisY, stickLogRightAxisX, stickLogRightAxisY;
    private final float[] stickLogLeftSectorPeak = new float[16], stickLogRightSectorPeak = new float[16];
    private int stickLogLeftSectors, stickLogRightSectors;
    private long stickLogDueMs;
    /** OR of every raw 32-bit button word seen since the last extents line: shows which bits this firmware sends. */
    private int stickLogButtonsSeen;
    // Stick stretch (off by default since fork.23): each axis is divided by the largest
    // deflection seen on it and the result is clamped to the unit circle. Extents start at a
    // floor and only grow, and persist per controller. Added in fork.21 because the BLE stick
    // passed a magnitude of 1.18 on diagonals; retired as a default on 2026-10-08 when the host
    // probe showed that Steam's "full circle" step accepts the wired unit's rounded square
    // (each axis parked at full deflection, per-axis clip) and stalls on a perfect circle
    // delivered at 250 reports/s, which is exactly what this stretch produced over a stream.
    private static final float STICK_EXTENT_FLOOR = 0.75f;
    private static final String STICK_EXTENT_PREFS = "steam_controller_ble_sticks";
    private float leftExtentX = STICK_EXTENT_FLOOR, leftExtentY = STICK_EXTENT_FLOOR;
    private float rightExtentX = STICK_EXTENT_FLOOR, rightExtentY = STICK_EXTENT_FLOOR;
    private boolean extentsDirty;
    protected boolean leftPadTouched, rightPadTouched;
    // Last forwarded pad position per pad, to skip MOVE packets that carry no change.
    private final float[] lastTouchX = new float[2], lastTouchY = new float[2], lastTouchPressure = new float[2];

    protected SteamControllerInput(int deviceId, UsbDriverListener listener, int productId,
                                   Context context, String calibrationKey, String transportName,
                                   boolean motionEnabled, boolean splitPads, int gripsMode, boolean stickRim) {
        super(deviceId, listener, 0x28DE, productId);
        this.inputContext = context.getApplicationContext();
        this.calibrationKey = calibrationKey;
        this.transportName = transportName;
        this.motionEnabled = motionEnabled;
        this.splitPads = splitPads;
        this.gripsMode = gripsMode;
        this.stickRim = stickRim;
        loadStickExtents();
        type = MoonBridge.LI_CTYPE_STEAM;
        capabilities = (short) (MoonBridge.LI_CCAP_ANALOG_TRIGGERS | MoonBridge.LI_CCAP_RUMBLE
                | MoonBridge.LI_CCAP_TOUCHPAD | (splitPads ? 0 : MoonBridge.LI_CCAP_DUAL_TOUCHPAD)
                | MoonBridge.LI_CCAP_BATTERY_STATE | MoonBridge.LI_CCAP_GRIP_SENSE | MoonBridge.LI_CCAP_STICK_TOUCH
                | MoonBridge.LI_CCAP_STEAM_HAPTIC | MoonBridge.LI_CCAP_SC26_BUTTONS
                | (motionEnabled ? (MoonBridge.LI_CCAP_GYRO | MoonBridge.LI_CCAP_ACCEL) : 0));
        supportedButtonFlags = SUPPORTED_BUTTONS;
    }

    protected void resetInputState() {
        reportedStateValid = false;
        if (calibrationKey == null) {
            leftExtentX = leftExtentY = rightExtentX = rightExtentY = STICK_EXTENT_FLOOR;
        }
        leftPadTouched = rightPadTouched = false;
    }

    // ----- report parsing -----

    /**
     * @param p the 0x45 state report without its report-id byte: p[0] is the sequence counter and
     *          every USB offset documented for the report is one less here.
     */
    protected void handleState(byte[] p) {
        handleState(p, 0, false);
    }

    /** USB includes a report ID; 0x47 inserts a 16-bit timestamp before the pads. */
    protected void handleState(byte[] p, int offset, boolean timestampedPads) {
        if (p.length - offset < 45) {
            return;
        }
        int buttons = u32(p, offset + 1);
        stickLogButtonsSeen |= buttons;
        int flags = 0;
        if (bit(buttons, BTN_A)) flags |= ControllerPacket.A_FLAG;
        if (bit(buttons, BTN_B)) flags |= ControllerPacket.B_FLAG;
        if (bit(buttons, BTN_X)) flags |= ControllerPacket.X_FLAG;
        if (bit(buttons, BTN_Y)) flags |= ControllerPacket.Y_FLAG;
        if (bit(buttons, BTN_DPAD_UP)) flags |= ControllerPacket.UP_FLAG;
        if (bit(buttons, BTN_DPAD_DOWN)) flags |= ControllerPacket.DOWN_FLAG;
        if (bit(buttons, BTN_DPAD_LEFT)) flags |= ControllerPacket.LEFT_FLAG;
        if (bit(buttons, BTN_DPAD_RIGHT)) flags |= ControllerPacket.RIGHT_FLAG;
        if (bit(buttons, BTN_LB)) flags |= ControllerPacket.LB_FLAG;
        if (bit(buttons, BTN_RB)) flags |= ControllerPacket.RB_FLAG;
        if (bit(buttons, BTN_MENU)) flags |= ControllerPacket.PLAY_FLAG;
        if (bit(buttons, BTN_VIEW)) flags |= ControllerPacket.BACK_FLAG;
        if (bit(buttons, BTN_LSTICK_CLICK)) flags |= ControllerPacket.LS_CLK_FLAG;
        if (bit(buttons, BTN_RSTICK_CLICK)) flags |= ControllerPacket.RS_CLK_FLAG;
        if (bit(buttons, BTN_STEAM)) flags |= ControllerPacket.SPECIAL_BUTTON_FLAG;
        if (bit(buttons, BTN_QUICK_ACCESS)) flags |= ControllerPacket.MISC_FLAG;
        switch (gripsMode) {
            case GRIPS_PADDLES:
                // Xbox Elite paddle order: P1 upper right, P2 upper left, P3 lower right, P4 lower left
                if (bit(buttons, BTN_GRIP_R_TOP)) flags |= ControllerPacket.PADDLE1_FLAG;
                if (bit(buttons, BTN_GRIP_L_TOP)) flags |= ControllerPacket.PADDLE2_FLAG;
                if (bit(buttons, BTN_GRIP_R_BOTTOM)) flags |= ControllerPacket.PADDLE3_FLAG;
                if (bit(buttons, BTN_GRIP_L_BOTTOM)) flags |= ControllerPacket.PADDLE4_FLAG;
                break;
            case GRIPS_DS4:
                // A DualShock 4 has no paddles; give the grips inputs the host can actually emulate
                if (bit(buttons, BTN_GRIP_L_TOP)) flags |= ControllerPacket.MISC_FLAG;       // Share
                if (bit(buttons, BTN_GRIP_R_TOP)) flags |= ControllerPacket.TOUCHPAD_FLAG;   // touchpad click
                if (bit(buttons, BTN_GRIP_L_BOTTOM)) flags |= ControllerPacket.LS_CLK_FLAG;  // L3
                if (bit(buttons, BTN_GRIP_R_BOTTOM)) flags |= ControllerPacket.RS_CLK_FLAG;  // R3
                break;
            default:
                break;
        }
        if (bit(buttons, BTN_LPAD_CLICK) || bit(buttons, BTN_RPAD_CLICK)) flags |= ControllerPacket.TOUCHPAD_FLAG;
        if (bit(buttons, BTN_LPAD_CLICK)) flags |= ControllerPacket.LEFT_PAD_CLICK_FLAG;
        if (bit(buttons, BTN_RPAD_CLICK)) flags |= ControllerPacket.RIGHT_PAD_CLICK_FLAG;
        if (bit(buttons, BTN_LT_FULL)) flags |= ControllerPacket.LEFT_TRIGGER_CLICK_FLAG;
        if (bit(buttons, BTN_RT_FULL)) flags |= ControllerPacket.RIGHT_TRIGGER_CLICK_FLAG;
        // Grip sense (held, not pressed): a Vibepollo host with the Steam Controller profile
        // puts these back on the virtual device so Steam's "gyro on grip" works; other hosts
        // ignore the two extension bits.
        if (bit(buttons, BTN_GRIP_L_TOUCH)) flags |= ControllerPacket.LEFT_GRIP_TOUCH_FLAG;
        if (bit(buttons, BTN_GRIP_R_TOUCH)) flags |= ControllerPacket.RIGHT_GRIP_TOUCH_FLAG;
        // Stick touch (capacitive, held not pressed) goes the same way; the host driver
        // otherwise guesses it from deflection and misses a thumb resting on the stick.
        if (bit(buttons, BTN_LSTICK_TOUCH)) flags |= ControllerPacket.LEFT_STICK_TOUCH_FLAG;
        if (bit(buttons, BTN_RSTICK_TOUCH)) flags |= ControllerPacket.RIGHT_STICK_TOUCH_FLAG;
        buttonFlags = flags;

        // Triggers: 0..32767 (SDL maps value*2-32768 onto the full axis)
        leftTrigger = clamp01(s16(p, offset + 5) / 32767f);
        rightTrigger = clamp01(s16(p, offset + 7) / 32767f);
        // Sticks: signed. The controller reports Y up-positive while ControllerHandler expects the
        // Android/Linux down-positive convention (it negates on the way out), so flip Y here.
        // Confirmed on hardware: without the flip both sticks were vertically inverted.
        leftStickX = s16(p, offset + 9) / 32767f;
        leftStickY = -s16(p, offset + 11) / 32767f;
        rightStickX = s16(p, offset + 13) / 32767f;
        rightStickY = -s16(p, offset + 15) / 32767f;
        trackStickExtents();
        if (stickRim) {
            calibrateSticks();
        }
        // The firmware streams at 250 Hz whether or not anything moved. An unchanged
        // state would still cost the handler's aggregation pass, a JNI call and a reliable
        // ENet packet (plus its ACK) each time; the host keeps the last state on its own.
        if (reportedStateValid && buttonFlags == reportedButtonFlags
                && leftTrigger == reportedLeftTrigger && rightTrigger == reportedRightTrigger
                && leftStickX == reportedLeftStickX && leftStickY == reportedLeftStickY
                && rightStickX == reportedRightStickX && rightStickY == reportedRightStickY) {
            // unchanged
        } else {
            reportedStateValid = true;
            reportedButtonFlags = buttonFlags;
            reportedLeftTrigger = leftTrigger;
            reportedRightTrigger = rightTrigger;
            reportedLeftStickX = leftStickX;
            reportedLeftStickY = leftStickY;
            reportedRightStickX = rightStickX;
            reportedRightStickY = rightStickY;
            reportInput();
        }

        // Touchpads: normalised 0..1 with (0,0) top-left, as SDL does; pressure 0..1
        boolean lTouch = bit(buttons, BTN_LPAD_TOUCH);
        boolean rTouch = bit(buttons, BTN_RPAD_TOUCH);
        leftPadTouched = updatePad((byte) 0, leftPadTouched, lTouch, s16(p, offset + 17 + (timestampedPads ? 2 : 0)), s16(p, offset + 19 + (timestampedPads ? 2 : 0)), u16(p, offset + 21 + (timestampedPads ? 2 : 0)));
        rightPadTouched = updatePad((byte) 1, rightPadTouched, rTouch, s16(p, offset + 23 + (timestampedPads ? 2 : 0)), s16(p, offset + 25 + (timestampedPads ? 2 : 0)), u16(p, offset + 27 + (timestampedPads ? 2 : 0)));

        if (motionEnabled) {
            // Raw IMU: gyro full scale 2000 deg/s, accel 2 g. Axis order follows SDL's Triton driver.
            final float gyroScale = 2000f / 32768f;
            final float accelScale = 2f * 9.80665f / 32768f;
            short gx = s16(p, offset + 39), gy = s16(p, offset + 41), gz = s16(p, offset + 43);
            short ax = s16(p, offset + 33), ay = s16(p, offset + 35), az = s16(p, offset + 37);
            gyroX = gx * gyroScale;
            gyroY = gz * gyroScale;
            gyroZ = -gy * gyroScale;
            accelX = ax * accelScale;
            accelY = az * accelScale;
            accelZ = -ay * accelScale;
            reportMotion();
        }
    }

    private boolean updatePad(byte pad, boolean wasTouched, boolean touched, short rawX, short rawY, int rawPressure) {
        // Split mode: both pads are fingers 0/1 on touchpad 0, each confined to its half.
        // Dual mode: pad 0/1 with finger 0 each (needs a host that knows LI_CCAP_DUAL_TOUCHPAD;
        // common-c drops touchpad 1 on hosts without the feature).
        byte touchpadIndex = splitPads ? 0 : pad;
        int pointerId = splitPads ? pad : 0;
        if (touched) {
            float x = rawX / 65536f + 0.5f;
            float y = -rawY / 65536f + 0.5f;
            if (splitPads) {
                x = pad == 0 ? x * 0.5f : 0.5f + x * 0.5f;
            }
            float pressure = clamp01(rawPressure / 32768f);
            if (wasTouched && x == lastTouchX[pad] && y == lastTouchY[pad] && pressure == lastTouchPressure[pad]) {
                // A resting finger produced 250 identical MOVE packets per second per pad.
                return true;
            }
            lastTouchX[pad] = x;
            lastTouchY[pad] = y;
            lastTouchPressure[pad] = pressure;
            reportTouch(touchpadIndex, wasTouched ? MoonBridge.LI_TOUCH_EVENT_MOVE : MoonBridge.LI_TOUCH_EVENT_DOWN, pointerId, x, y, pressure);
        } else if (wasTouched) {
            reportTouch(touchpadIndex, MoonBridge.LI_TOUCH_EVENT_UP, pointerId, 0, 0, 0);
        }
        return touched;
    }

    /**
     * Battery characteristic / report 0x43 without the id: [0] charge state, [1] percent.
     * The charge state follows the firmware's EChargeState (libvirtualgamepad sc26_usb.h):
     * 0 reset (not measured yet), 1 discharging, 2 charging, 3 source validate, 4 done.
     */
    protected void handleBattery(byte[] data) {
        handleBattery(data, 0);
    }

    protected void handleBattery(byte[] data, int offset) {
        if (data == null || data.length - offset < 2) {
            return;
        }
        int chargeState = data[offset] & 0xFF;
        int percent = Math.min(100, data[offset + 1] & 0xFF);
        byte state;
        switch (chargeState) {
            case 1:
                state = MoonBridge.LI_BATTERY_STATE_DISCHARGING;
                break;
            case 2:
            case 3:
                state = MoonBridge.LI_BATTERY_STATE_CHARGING;
                break;
            case 4:
                state = MoonBridge.LI_BATTERY_STATE_FULL;
                break;
            default:
                state = MoonBridge.LI_BATTERY_STATE_UNKNOWN;
                break;
        }
        reportBattery(state, (byte) percent);
    }

    // ----- helpers -----

    /**
     * Diagnostics for Steam's "move the stick in a full circle" calibration step, which stalled on
     * the virtual Steam Controller: logs each stick's peak magnitude and how many of 16 angular
     * sectors it visited, so the raw BLE range can be compared with what the host device reports.
     */
    private void trackStickExtents() {
        float lm = (float) Math.hypot(leftStickX, leftStickY);
        float rm = (float) Math.hypot(rightStickX, rightStickY);
        if (lm > 0.5f) {
            stickLogLeftMax = Math.max(stickLogLeftMax, lm);
            stickLogLeftAxisX = Math.max(stickLogLeftAxisX, Math.abs(leftStickX));
            stickLogLeftAxisY = Math.max(stickLogLeftAxisY, Math.abs(leftStickY));
            int sec = sector(leftStickX, leftStickY);
            stickLogLeftSectors |= 1 << sec;
            stickLogLeftSectorPeak[sec] = Math.max(stickLogLeftSectorPeak[sec], lm);
        }
        if (rm > 0.5f) {
            stickLogRightMax = Math.max(stickLogRightMax, rm);
            stickLogRightAxisX = Math.max(stickLogRightAxisX, Math.abs(rightStickX));
            stickLogRightAxisY = Math.max(stickLogRightAxisY, Math.abs(rightStickY));
            int sec = sector(rightStickX, rightStickY);
            stickLogRightSectors |= 1 << sec;
            stickLogRightSectorPeak[sec] = Math.max(stickLogRightSectorPeak[sec], rm);
        }
        int gripBits = (1 << BTN_GRIP_L_TOUCH) | (1 << BTN_GRIP_R_TOUCH);
        if ((stickLogLeftSectors | stickLogRightSectors) == 0 && (stickLogButtonsSeen & gripBits) == 0) {
            return;
        }
        long now = SystemClock.uptimeMillis();
        if (now < stickLogDueMs) {
            return;
        }
        LimeLog.info("Steam Controller " + transportName + ": stick extents (raw): left "
                + describeStick(stickLogLeftMax, stickLogLeftAxisX, stickLogLeftAxisY, stickLogLeftSectors, stickLogLeftSectorPeak)
                + ", right "
                + describeStick(stickLogRightMax, stickLogRightAxisX, stickLogRightAxisY, stickLogRightSectors, stickLogRightSectorPeak)
                + String.format(Locale.ROOT, "; rim calibration %s, extents left %.3f/%.3f right %.3f/%.3f",
                        stickRim ? "on" : "off", leftExtentX, leftExtentY, rightExtentX, rightExtentY)
                + String.format(Locale.ROOT, "; raw buttons seen 0x%08x (grip touch L %s R %s, stick touch L %s R %s)", stickLogButtonsSeen,
                        bit(stickLogButtonsSeen, BTN_GRIP_L_TOUCH) ? "yes" : "no",
                        bit(stickLogButtonsSeen, BTN_GRIP_R_TOUCH) ? "yes" : "no",
                        bit(stickLogButtonsSeen, BTN_LSTICK_TOUCH) ? "yes" : "no",
                        bit(stickLogButtonsSeen, BTN_RSTICK_TOUCH) ? "yes" : "no"));
        stickLogDueMs = now + 10_000;
        stickLogButtonsSeen = 0;
        stickLogLeftMax = stickLogRightMax = 0;
        stickLogLeftAxisX = stickLogLeftAxisY = stickLogRightAxisX = stickLogRightAxisY = 0;
        stickLogLeftSectors = stickLogRightSectors = 0;
        java.util.Arrays.fill(stickLogLeftSectorPeak, 0);
        java.util.Arrays.fill(stickLogRightSectorPeak, 0);
    }

    /** "max 1.167 (|x| 0.83 |y| 0.84) in 16/16 sectors, sector peaks E 0.83 NE 1.17 N 0.84 ..." */
    private static String describeStick(float max, float axisX, float axisY, int sectors, float[] sectorPeak) {
        StringBuilder sb = new StringBuilder(String.format(Locale.ROOT, "max %.3f (|x| %.2f |y| %.2f) in %d/16 sectors",
                max, axisX, axisY, Integer.bitCount(sectors)));
        if (sectors != 0) {
            // Sectors are 22.5 deg wide starting at -180 deg (atan2 of the down-positive Y used here);
            // report the eight compass points as the peak of the two sectors around each.
            final String[] names = {"W", "NW", "N", "NE", "E", "SE", "S", "SW"};
            sb.append(", peaks");
            for (int i = 0; i < 8; i++) {
                int a = (2 * i + 15) % 16, b = (2 * i) % 16;
                float peak = Math.max(sectorPeak[a], sectorPeak[b]);
                sb.append(' ').append(names[i]).append(' ').append(String.format(Locale.ROOT, "%.2f", peak));
            }
        }
        return sb.toString();
    }

    /** Rescales both sticks to the rim: see the field comment on STICK_EXTENT_FLOOR. */
    private void calibrateSticks() {
        float lx = Math.abs(leftStickX), ly = Math.abs(leftStickY);
        float rx = Math.abs(rightStickX), ry = Math.abs(rightStickY);
        if (lx > leftExtentX) { leftExtentX = lx; extentsDirty = true; }
        if (ly > leftExtentY) { leftExtentY = ly; extentsDirty = true; }
        if (rx > rightExtentX) { rightExtentX = rx; extentsDirty = true; }
        if (ry > rightExtentY) { rightExtentY = ry; extentsDirty = true; }
        float x = leftStickX / leftExtentX, y = leftStickY / leftExtentY;
        float m = (float) Math.hypot(x, y);
        if (m > 1f) { x /= m; y /= m; }
        leftStickX = x; leftStickY = y;
        x = rightStickX / rightExtentX; y = rightStickY / rightExtentY;
        m = (float) Math.hypot(x, y);
        if (m > 1f) { x /= m; y /= m; }
        rightStickX = x; rightStickY = y;
    }

    private void loadStickExtents() {
        if (calibrationKey == null) return;
        try {
            SharedPreferences prefs = inputContext.getSharedPreferences(STICK_EXTENT_PREFS, Context.MODE_PRIVATE);
            String key = calibrationKey;
            leftExtentX = Math.max(STICK_EXTENT_FLOOR, Math.min(1f, prefs.getFloat(key + ".lx", STICK_EXTENT_FLOOR)));
            leftExtentY = Math.max(STICK_EXTENT_FLOOR, Math.min(1f, prefs.getFloat(key + ".ly", STICK_EXTENT_FLOOR)));
            rightExtentX = Math.max(STICK_EXTENT_FLOOR, Math.min(1f, prefs.getFloat(key + ".rx", STICK_EXTENT_FLOOR)));
            rightExtentY = Math.max(STICK_EXTENT_FLOOR, Math.min(1f, prefs.getFloat(key + ".ry", STICK_EXTENT_FLOOR)));
        } catch (RuntimeException e) {
            LimeLog.warning("Steam Controller " + transportName + ": stick extents not loaded: " + e);
        }
    }

    protected void saveStickExtents() {
        if (!extentsDirty || calibrationKey == null) {
            return;
        }
        extentsDirty = false;
        try {
            String key = calibrationKey;
            inputContext.getSharedPreferences(STICK_EXTENT_PREFS, Context.MODE_PRIVATE).edit()
                    .putFloat(key + ".lx", leftExtentX).putFloat(key + ".ly", leftExtentY)
                    .putFloat(key + ".rx", rightExtentX).putFloat(key + ".ry", rightExtentY)
                    .apply();
            LimeLog.info(String.format(Locale.ROOT, "Steam Controller " + transportName + ": stick extents saved: left %.3f/%.3f right %.3f/%.3f",
                    leftExtentX, leftExtentY, rightExtentX, rightExtentY));
        } catch (RuntimeException e) {
            LimeLog.warning("Steam Controller " + transportName + ": stick extents not saved: " + e);
        }
    }

    private static int sector(float x, float y) {
        double a = Math.atan2(y, x);   // -pi..pi
        int sector = (int) Math.floor((a + Math.PI) / (2 * Math.PI) * 16);
        return Math.min(15, Math.max(0, sector));
    }

    private static boolean bit(int field, int index) {
        return (field & (1 << index)) != 0;
    }

    private static short s16(byte[] p, int i) {
        return (short) ((p[i] & 0xFF) | (p[i + 1] << 8));
    }

    protected static int u16(byte[] p, int i) {
        return (p[i] & 0xFF) | ((p[i + 1] & 0xFF) << 8);
    }

    private static int u32(byte[] p, int i) {
        return (p[i] & 0xFF) | ((p[i + 1] & 0xFF) << 8) | ((p[i + 2] & 0xFF) << 16) | ((p[i + 3] & 0xFF) << 24);
    }

    private static float clamp01(float v) {
        return v < 0 ? 0 : (v > 1 ? 1 : v);
    }

}
