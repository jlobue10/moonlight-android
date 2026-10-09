package com.limelight.binding.input.driver.ble;

import android.annotation.SuppressLint;
import android.bluetooth.BluetoothDevice;
import android.bluetooth.BluetoothGatt;
import android.bluetooth.BluetoothGattCallback;
import android.bluetooth.BluetoothGattCharacteristic;
import android.bluetooth.BluetoothGattDescriptor;
import android.bluetooth.BluetoothGattService;
import android.bluetooth.BluetoothProfile;
import android.content.Context;
import android.content.SharedPreferences;
import android.os.Build;
import android.os.Handler;
import android.os.SystemClock;
import android.os.Looper;
import android.os.SystemClock;

import com.limelight.LimeLog;
import com.limelight.binding.input.driver.AbstractController;
import com.limelight.binding.input.driver.UsbDriverListener;
import com.limelight.nvstream.input.ControllerPacket;
import com.limelight.nvstream.jni.MoonBridge;

import java.util.ArrayDeque;
import java.util.Locale;
import java.util.UUID;

/**
 * Driver for the 2026 Steam Controller ("Ibex"/"Triton", VID 0x28DE PID 0x1303 over Bluetooth LE).
 *
 * Without Steam the controller exposes no standard gamepad: its HID report is a vendor collection
 * that Android ignores, and in "lizard mode" it only emulates a keyboard and mouse. Android claims
 * the HID service, but Valve's vendor GATT service is open to apps, so this driver talks to the
 * controller directly: it subscribes to the state notifications, switches lizard mode and the
 * firmware's Steam watchdog off, enables raw IMU output, and feeds the parsed state into the same
 * {@link UsbDriverListener} path the USB Xbox drivers use. The host sees an {@code LI_CTYPE_STEAM}
 * controller with two touchpads, gyro/accel, four grip paddles and battery state.
 *
 * Report layout (report id 0x45, "MTU, no quaternion", 46 bytes with the id; BLE delivers it
 * without the leading id byte) and the command set follow the Linux {@code hid-steam} driver and
 * SDL's {@code SDL_hidapi_steam_triton.c}; the GATT characteristic ranges were documented by the
 * SteamController-Android project (MIT). See docs/STEAM_CONTROLLER.md.
 */
public class SteamControllerBle extends AbstractController {

    public static final int VENDOR_ID_VALVE = 0x28de;
    public static final int PRODUCT_ID_IBEX_BLE = 0x1303;

    static final UUID VALVE_SERVICE = UUID.fromString("100f6c32-1735-4313-b402-38567131e5f3");
    private static final String VALVE_UUID_TAIL = "-1735-4313-b402-38567131e5f3";
    private static final long NOTIFY_SHORT_LOW = 0x100f6c75L;
    private static final long NOTIFY_SHORT_HIGH = 0x100f6c7aL;
    private static final long WRITE_SHORT_LOW = 0x100f6cb5L;
    private static final long WRITE_SHORT_HIGH = 0x100f6cbeL;
    private static final long BATTERY_SHORT = 0x100f6c78L;
    private static final long PING_SHORT = 0x100f6c79L;
    private static final UUID CCCD = UUID.fromString("00002902-0000-1000-8000-00805f9b34fb");
    private static final int DESIRED_MTU = 100;

    // Command ids (hid-steam.c / SDL controller_constants.h)
    private static final byte ID_CLEAR_DIGITAL_MAPPINGS = (byte) 0x81;
    private static final byte ID_SET_DEFAULT_DIGITAL_MAPPINGS = (byte) 0x85;
    private static final byte ID_SET_SETTINGS_VALUES = (byte) 0x87;
    private static final byte ID_LOAD_DEFAULT_SETTINGS = (byte) 0x8E;
    private static final byte ID_TRIGGER_HAPTIC_PULSE = (byte) 0x8F;
    // Setting ids (index in the firmware settings enum)
    private static final int SETTING_LIZARD_MODE = 9;
    private static final int SETTING_IMU_MODE = 48;
    private static final int SETTING_STEAM_WATCHDOG_ENABLE = 71;
    private static final int IMU_MODE_RAW_ACCEL = 0x08;
    private static final int IMU_MODE_RAW_GYRO = 0x10;

    /** The firmware re-arms lizard mode when it thinks Steam is gone; keep telling it otherwise. */
    private static final long KEEPALIVE_INTERVAL_MS = 1000;
    private static final long RECONNECT_DELAY_MS = 2000;
    private static final int MAX_RECONNECT_ATTEMPTS = 5;
    /** Haptic pulse period (~160 Hz); magnitude is expressed as duty cycle. */
    private static final int RUMBLE_PERIOD_US = 6250;
    /**
     * Rumble is driven the way Steam and the kernel drive this controller: short pulse trains that
     * are re-issued while the host's rumble stays non-zero, so a lost "motors off" event can only
     * ever leave the pads buzzing for one train. Each train outlives the refresh interval slightly
     * so the drive is continuous.
     */
    private static final int RUMBLE_REFRESH_MS = 100;
    private static final int RUMBLE_TRAIN_MS = 150;
    /** Hold limit of 0 means "until the host says stop" (the pre-fork.14 behaviour). */
    public static final int RUMBLE_HOLD_UNLIMITED = 0;

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
            ControllerPacket.LEFT_STICK_TOUCH_FLAG | ControllerPacket.RIGHT_STICK_TOUCH_FLAG;

    private final Context context;
    private final BluetoothDevice device;
    private final boolean motionEnabled;
    /**
     * Hosts without Steam Controller support emulate a DualShock 4, whose single touchpad Steam
     * Input splits into a left and a right half. With splitPads the two physical pads are sent as
     * two fingers on one pad (left pad → left half, right pad → right half) so that split lines up;
     * without it they go out as touchpad 0 and 1 for hosts that understand LI_CCAP_DUAL_TOUCHPAD.
     */
    private final boolean splitPads;
    public static final int GRIPS_PADDLES = 0;   // L4/L5/R4/R5 as PADDLE1..4 (Xbox Elite style)
    public static final int GRIPS_DS4 = 1;       // extras a DualShock host can use: Share, touchpad click, L3, R3
    public static final int GRIPS_OFF = 2;
    private final int gripsMode;
    private final int rumbleHoldMs;
    private final boolean stickRim;
    private final Handler handler = new Handler(Looper.getMainLooper());

    private BluetoothGatt gatt;
    private BluetoothGattCharacteristic writeChar;
    private BluetoothGattCharacteristic batteryChar;
    private final ArrayDeque<BluetoothGattCharacteristic> pendingSubscriptions = new ArrayDeque<>();
    private final ArrayDeque<byte[]> writeQueue = new ArrayDeque<>();
    private boolean writeBusy;
    private boolean mtuRequested;
    private boolean announced;
    private volatile boolean stopped;
    private int reconnectAttempts;
    private boolean everConnected;
    // Stick extent diagnostics (stream log, every 10 s while a stick is away from centre).
    // All of it is measured on the raw BLE values, before the rim calibration below.
    private float stickLogLeftMax, stickLogRightMax;
    private float stickLogLeftAxisX, stickLogLeftAxisY, stickLogRightAxisX, stickLogRightAxisY;
    private final float[] stickLogLeftSectorPeak = new float[16], stickLogRightSectorPeak = new float[16];
    private int stickLogLeftSectors, stickLogRightSectors;
    private long stickLogDueMs;
    /** OR of every raw 32-bit button word seen since the last extents line: shows which bits this firmware sends over BLE. */
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
    private boolean leftPadTouched, rightPadTouched;
    // Rumble state; only touched on the handler thread.
    private float rumbleLow, rumbleHigh;
    private boolean rumbleActive;
    private long rumbleDeadline;

    private final Runnable keepAlive = new Runnable() {
        @Override
        public void run() {
            if (stopped || gatt == null || writeChar == null) {
                return;
            }
            enqueueWrite(settings(SETTING_LIZARD_MODE, 0));
            handler.postDelayed(this, KEEPALIVE_INTERVAL_MS);
        }
    };

    public SteamControllerBle(int deviceId, UsbDriverListener listener, Context context,
                              BluetoothDevice device, boolean motionEnabled, boolean splitPads, int gripsMode,
                              int rumbleHoldMs, boolean stickRim) {
        super(deviceId, listener, VENDOR_ID_VALVE, PRODUCT_ID_IBEX_BLE);
        this.context = context.getApplicationContext();
        this.device = device;
        this.motionEnabled = motionEnabled;
        this.splitPads = splitPads;
        this.gripsMode = gripsMode;
        this.rumbleHoldMs = Math.max(0, rumbleHoldMs);
        this.stickRim = stickRim;
        loadStickExtents();
        this.type = MoonBridge.LI_CTYPE_STEAM;
        this.capabilities = (short) (MoonBridge.LI_CCAP_ANALOG_TRIGGERS | MoonBridge.LI_CCAP_RUMBLE
                | MoonBridge.LI_CCAP_TOUCHPAD | (splitPads ? 0 : MoonBridge.LI_CCAP_DUAL_TOUCHPAD)
                | MoonBridge.LI_CCAP_BATTERY_STATE | MoonBridge.LI_CCAP_GRIP_SENSE | MoonBridge.LI_CCAP_STICK_TOUCH
                | (motionEnabled ? (MoonBridge.LI_CCAP_GYRO | MoonBridge.LI_CCAP_ACCEL) : 0));
        this.supportedButtonFlags = SUPPORTED_BUTTONS;
    }

    public String getAddress() {
        return device.getAddress();
    }

    /** True while a GATT connection attempt or link exists. */
    public boolean isLinkUp() {
        return gatt != null;
    }

    // ----- lifecycle -----

    @SuppressLint("MissingPermission")   // the manager checks BLUETOOTH_CONNECT before creating us
    @Override
    public boolean start() {
        stopped = false;
        LimeLog.info("Steam Controller BLE: connecting to " + device.getAddress());
        try {
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.M) {
                gatt = device.connectGatt(context, false, gattCallback, BluetoothDevice.TRANSPORT_LE);
            } else {
                gatt = device.connectGatt(context, false, gattCallback);
            }
        } catch (SecurityException e) {
            LimeLog.warning("Steam Controller BLE: no Bluetooth permission: " + e.getMessage());
            return false;
        }
        return gatt != null;
    }

    @SuppressLint("MissingPermission")
    @Override
    public void stop() {
        stopped = true;
        handler.removeCallbacksAndMessages(null);
        saveStickExtents();
        BluetoothGatt g = gatt;
        gatt = null;
        if (g != null) {
            try {
                // Give the controller its keyboard/mouse emulation back for the rest of the system.
                if (writeChar != null) {
                    writeChar.setValue(new byte[]{ID_SET_DEFAULT_DIGITAL_MAPPINGS});
                    writeChar.setWriteType(BluetoothGattCharacteristic.WRITE_TYPE_NO_RESPONSE);
                    g.writeCharacteristic(writeChar);
                    writeChar.setValue(new byte[]{ID_LOAD_DEFAULT_SETTINGS});
                    g.writeCharacteristic(writeChar);
                }
                g.disconnect();
                g.close();
            } catch (SecurityException | IllegalStateException e) {
                LimeLog.warning("Steam Controller BLE: close failed: " + e.getMessage());
            }
        }
        if (announced) {
            announced = false;
            notifyDeviceRemoved();
        }
    }

    // ----- GATT -----

    private final BluetoothGattCallback gattCallback = new BluetoothGattCallback() {
        @SuppressLint("MissingPermission")
        @Override
        public void onConnectionStateChange(BluetoothGatt g, int status, int newState) {
            if (stopped) {
                return;
            }
            if (newState == BluetoothProfile.STATE_CONNECTED && status == BluetoothGatt.GATT_SUCCESS) {
                LimeLog.info("Steam Controller BLE: connected, negotiating");
                reconnectAttempts = 0;
                everConnected = true;
                mtuRequested = true;
                g.requestConnectionPriority(BluetoothGatt.CONNECTION_PRIORITY_HIGH);
                if (!g.requestMtu(DESIRED_MTU)) {
                    mtuRequested = false;
                    g.discoverServices();
                }
            } else if (newState == BluetoothProfile.STATE_DISCONNECTED) {
                LimeLog.info("Steam Controller BLE: disconnected (status " + status + ")");
                onLinkLost(g);
            }
        }

        @SuppressLint("MissingPermission")
        @Override
        public void onMtuChanged(BluetoothGatt g, int mtu, int status) {
            // The stack may report this twice; discover services once.
            if (mtuRequested) {
                mtuRequested = false;
                LimeLog.info("Steam Controller BLE: MTU " + mtu);
                g.discoverServices();
            }
        }

        @SuppressLint("MissingPermission")
        @Override
        public void onServicesDiscovered(BluetoothGatt g, int status) {
            if (stopped) {
                return;
            }
            BluetoothGattService service = g.getService(VALVE_SERVICE);
            if (status != BluetoothGatt.GATT_SUCCESS || service == null) {
                LimeLog.warning("Steam Controller BLE: Valve service not found (status " + status + ")");
                onLinkLost(g);
                return;
            }
            synchronized (SteamControllerBle.this) {
                pendingSubscriptions.clear();
                writeChar = null;
                batteryChar = null;
                for (BluetoothGattCharacteristic ch : service.getCharacteristics()) {
                    long shortId = shortUuid(ch.getUuid());
                    int props = ch.getProperties();
                    if (shortId >= NOTIFY_SHORT_LOW && shortId <= NOTIFY_SHORT_HIGH
                            && (props & BluetoothGattCharacteristic.PROPERTY_NOTIFY) != 0) {
                        pendingSubscriptions.add(ch);
                    }
                    if (shortId == BATTERY_SHORT) {
                        batteryChar = ch;
                    }
                    if (writeChar == null && shortId >= WRITE_SHORT_LOW && shortId <= WRITE_SHORT_HIGH
                            && (props & (BluetoothGattCharacteristic.PROPERTY_WRITE
                            | BluetoothGattCharacteristic.PROPERTY_WRITE_NO_RESPONSE)) != 0) {
                        writeChar = ch;
                    }
                }
            }
            LimeLog.info("Steam Controller BLE: " + pendingSubscriptions.size() + " notify characteristics, command characteristic "
                    + (writeChar != null ? writeChar.getUuid() : "none"));
            if (writeChar == null || pendingSubscriptions.isEmpty()) {
                onLinkLost(g);
                return;
            }
            subscribeNext(g);
        }

        @Override
        public void onDescriptorWrite(BluetoothGatt g, BluetoothGattDescriptor descriptor, int status) {
            if (!subscribeNext(g)) {
                onReady(g);
            }
        }

        @Override
        public void onCharacteristicWrite(BluetoothGatt g, BluetoothGattCharacteristic ch, int status) {
            if (status != BluetoothGatt.GATT_SUCCESS) {
                LimeLog.warning("Steam Controller BLE: write failed, status " + status);
            }
            synchronized (SteamControllerBle.this) {
                writeBusy = false;
            }
            flushWrites();
        }

        @Override
        public void onCharacteristicRead(BluetoothGatt g, BluetoothGattCharacteristic ch, int status) {
            if (status == BluetoothGatt.GATT_SUCCESS && shortUuid(ch.getUuid()) == BATTERY_SHORT) {
                handleBattery(ch.getValue());
            }
        }

        @Override
        public void onCharacteristicChanged(BluetoothGatt g, BluetoothGattCharacteristic ch) {
            byte[] data = ch.getValue();
            if (data == null) {
                return;
            }
            long shortId = shortUuid(ch.getUuid());
            if (shortId == BATTERY_SHORT) {
                handleBattery(data);
            } else if (shortId == PING_SHORT) {
                // ack/ping-pong tied to our writes; nothing to do
            } else if (data.length >= 40) {
                handleState(data);
            }
        }
    };

    @SuppressLint("MissingPermission")
    private boolean subscribeNext(BluetoothGatt g) {
        BluetoothGattCharacteristic ch;
        synchronized (this) {
            ch = pendingSubscriptions.poll();
        }
        if (ch == null) {
            return false;
        }
        g.setCharacteristicNotification(ch, true);
        BluetoothGattDescriptor cccd = ch.getDescriptor(CCCD);
        if (cccd == null) {
            // Nothing to wait for; move on synchronously
            return subscribeNext(g) || false;
        }
        cccd.setValue(BluetoothGattDescriptor.ENABLE_NOTIFICATION_VALUE);
        g.writeDescriptor(cccd);
        return true;
    }

    /** All notifications are on: take the controller out of lizard mode and announce it. */
    @SuppressLint("MissingPermission")
    private void onReady(BluetoothGatt g) {
        if (stopped) {
            return;
        }
        enqueueWrite(new byte[]{ID_CLEAR_DIGITAL_MAPPINGS});
        enqueueWrite(settings(SETTING_LIZARD_MODE, 0, SETTING_STEAM_WATCHDOG_ENABLE, 0));
        enqueueWrite(settings(SETTING_IMU_MODE, motionEnabled ? (IMU_MODE_RAW_ACCEL | IMU_MODE_RAW_GYRO) : 0));
        if (batteryChar != null) {
            handler.post(() -> {
                try {
                    if (gatt != null) gatt.readCharacteristic(batteryChar);
                } catch (SecurityException ignored) {
                }
            });
        }
        handler.removeCallbacks(keepAlive);
        handler.postDelayed(keepAlive, KEEPALIVE_INTERVAL_MS);
        if (!announced) {
            announced = true;
            LimeLog.info("Steam Controller BLE: ready, reporting as LI_CTYPE_STEAM");
            notifyDeviceAdded();
        }
    }

    @SuppressLint("MissingPermission")
    private void onLinkLost(BluetoothGatt g) {
        handler.removeCallbacks(keepAlive);
        synchronized (this) {
            writeQueue.clear();
            writeBusy = false;
        }
        if (announced) {
            announced = false;
            // Release any touches the host still thinks are down
            if (leftPadTouched) reportTouch((byte) 0, MoonBridge.LI_TOUCH_EVENT_UP, 0, 0, 0, 0);
            if (rightPadTouched) reportTouch(splitPads ? (byte) 0 : (byte) 1, MoonBridge.LI_TOUCH_EVENT_UP, splitPads ? 1 : 0, 0, 0, 0);
            leftPadTouched = rightPadTouched = false;
            notifyDeviceRemoved();
        }
        try {
            g.close();
        } catch (SecurityException | IllegalStateException ignored) {
        }
        if (g == gatt) {
            gatt = null;
        }
        if (!stopped && !everConnected) {
            // A bonded controller that is switched off or out of range: connectGatt() times out with
            // status 133 after ~30 s. Do not loop on it; the manager restarts us when the system sees
            // it connect (ACL_CONNECTED).
            LimeLog.info("Steam Controller BLE: " + device.getAddress() + " not reachable; waiting for it to connect");
            return;
        }
        if (!stopped && reconnectAttempts++ < MAX_RECONNECT_ATTEMPTS) {
            LimeLog.info("Steam Controller BLE: reconnecting in " + RECONNECT_DELAY_MS + " ms (attempt " + reconnectAttempts + ")");
            handler.postDelayed(() -> {
                if (!stopped && gatt == null) {
                    start();
                }
            }, RECONNECT_DELAY_MS);
        }
    }

    // ----- commands -----

    /** ID_SET_SETTINGS_VALUES: 0x87, len, then (id, lo, hi) triples. */
    private static byte[] settings(int... idValuePairs) {
        int n = idValuePairs.length / 2;
        byte[] cmd = new byte[2 + 3 * n];
        cmd[0] = ID_SET_SETTINGS_VALUES;
        cmd[1] = (byte) (3 * n);
        for (int i = 0; i < n; i++) {
            int id = idValuePairs[2 * i], value = idValuePairs[2 * i + 1];
            cmd[2 + 3 * i] = (byte) id;
            cmd[3 + 3 * i] = (byte) (value & 0xFF);
            cmd[4 + 3 * i] = (byte) ((value >> 8) & 0xFF);
        }
        return cmd;
    }

    private void enqueueWrite(byte[] cmd) {
        synchronized (this) {
            if (writeQueue.size() > 32) {
                writeQueue.pollFirst();   // drop the oldest rather than grow without bound
            }
            writeQueue.add(cmd);
        }
        flushWrites();
    }

    @SuppressLint("MissingPermission")
    private void flushWrites() {
        BluetoothGatt g = gatt;
        BluetoothGattCharacteristic ch = writeChar;
        byte[] cmd;
        synchronized (this) {
            if (writeBusy || g == null || ch == null || stopped) {
                return;
            }
            cmd = writeQueue.poll();
            if (cmd == null) {
                return;
            }
            writeBusy = true;
        }
        try {
            ch.setWriteType(BluetoothGattCharacteristic.WRITE_TYPE_DEFAULT);
            ch.setValue(cmd);
            if (!g.writeCharacteristic(ch)) {
                synchronized (this) {
                    writeBusy = false;
                }
            }
        } catch (SecurityException | IllegalStateException e) {
            synchronized (this) {
                writeBusy = false;
            }
        }
    }

    @Override
    public void rumble(short lowFreqMotor, short highFreqMotor) {
        final float low = (lowFreqMotor & 0xFFFF) / 65535f;
        final float high = (highFreqMotor & 0xFFFF) / 65535f;
        handler.post(() -> {
            rumbleLow = low;
            rumbleHigh = high;
            handler.removeCallbacks(rumbleRefresh);
            if (low <= 0.01f && high <= 0.01f) {
                if (rumbleActive) {
                    rumbleActive = false;
                    enqueueWrite(hapticPulse(SIDE_LEFT, 0f, 0));
                    enqueueWrite(hapticPulse(SIDE_RIGHT, 0f, 0));
                }
                return;
            }
            // Every host update, even a repeat of the same value, restarts the hold window. Hosts
            // that forward Steam's UI haptics on a DualSense have been seen to never send the
            // matching "motors off", so without a limit the pads would buzz until the next event.
            rumbleDeadline = rumbleHoldMs == RUMBLE_HOLD_UNLIMITED
                    ? Long.MAX_VALUE : SystemClock.uptimeMillis() + rumbleHoldMs;
            rumbleActive = true;
            rumbleRefresh.run();
        });
    }

    private final Runnable rumbleRefresh = new Runnable() {
        @Override
        public void run() {
            if (!rumbleActive || stopped) {
                return;
            }
            long now = SystemClock.uptimeMillis();
            if (now >= rumbleDeadline) {
                rumbleActive = false;
                enqueueWrite(hapticPulse(SIDE_LEFT, 0f, 0));
                enqueueWrite(hapticPulse(SIDE_RIGHT, 0f, 0));
                return;
            }
            // Bound the train to the hold window so the last one ends on time by itself.
            int trainMs = (int) Math.min(RUMBLE_TRAIN_MS, Math.max(1, rumbleDeadline - now));
            int repeat = Math.max(1, trainMs * 1000 / RUMBLE_PERIOD_US);
            enqueueWrite(hapticPulse(SIDE_LEFT, rumbleLow, repeat));
            enqueueWrite(hapticPulse(SIDE_RIGHT, rumbleHigh, repeat));
            handler.postDelayed(this, Math.min(RUMBLE_REFRESH_MS, Math.max(1, rumbleDeadline - now)));
        }
    };

    /** Pulse side ids; the firmware numbers them the other way round from the kernel's pad ids. */
    private static final byte SIDE_RIGHT = 0;
    private static final byte SIDE_LEFT = 1;

    /** ID_TRIGGER_HAPTIC_PULSE: side, on_us, off_us, repeat count (0 = stop the running train). */
    private static byte[] hapticPulse(byte side, float magnitude, int repeat) {
        int onUs = 0, offUs = 0;
        if (magnitude > 0.01f && repeat > 0) {
            float duty = 0.25f + 0.72f * Math.min(1f, magnitude);
            onUs = (int) (RUMBLE_PERIOD_US * duty);
            offUs = RUMBLE_PERIOD_US - onUs;
        } else {
            repeat = 0;
        }
        return new byte[]{
                ID_TRIGGER_HAPTIC_PULSE, side,
                (byte) onUs, (byte) (onUs >> 8),
                (byte) offUs, (byte) (offUs >> 8),
                (byte) repeat, (byte) (repeat >> 8)};
    }

    @Override
    public void rumbleTriggers(short leftTrigger, short rightTrigger) {
        // No trigger motors on this controller
    }

    // ----- report parsing -----

    /**
     * @param p the 0x45 state report without its report-id byte: p[0] is the sequence counter and
     *          every USB offset documented for the report is one less here.
     */
    private void handleState(byte[] p) {
        if (p.length < 45) {
            return;
        }
        int buttons = u32(p, 1);
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
        leftTrigger = clamp01(s16(p, 5) / 32767f);
        rightTrigger = clamp01(s16(p, 7) / 32767f);
        // Sticks: signed. The controller reports Y up-positive while ControllerHandler expects the
        // Android/Linux down-positive convention (it negates on the way out), so flip Y here.
        // Confirmed on hardware: without the flip both sticks were vertically inverted.
        leftStickX = s16(p, 9) / 32767f;
        leftStickY = -s16(p, 11) / 32767f;
        rightStickX = s16(p, 13) / 32767f;
        rightStickY = -s16(p, 15) / 32767f;
        trackStickExtents();
        if (stickRim) {
            calibrateSticks();
        }
        reportInput();

        // Touchpads: normalised 0..1 with (0,0) top-left, as SDL does; pressure 0..1
        boolean lTouch = bit(buttons, BTN_LPAD_TOUCH);
        boolean rTouch = bit(buttons, BTN_RPAD_TOUCH);
        leftPadTouched = updatePad((byte) 0, leftPadTouched, lTouch, s16(p, 17), s16(p, 19), u16(p, 21));
        rightPadTouched = updatePad((byte) 1, rightPadTouched, rTouch, s16(p, 23), s16(p, 25), u16(p, 27));

        if (motionEnabled) {
            // Raw IMU: gyro full scale 2000 deg/s, accel 2 g. Axis order follows SDL's Triton driver.
            final float gyroScale = 2000f / 32768f;
            final float accelScale = 2f * 9.80665f / 32768f;
            short gx = s16(p, 39), gy = s16(p, 41), gz = s16(p, 43);
            short ax = s16(p, 33), ay = s16(p, 35), az = s16(p, 37);
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
            reportTouch(touchpadIndex, wasTouched ? MoonBridge.LI_TOUCH_EVENT_MOVE : MoonBridge.LI_TOUCH_EVENT_DOWN, pointerId, x, y, pressure);
        } else if (wasTouched) {
            reportTouch(touchpadIndex, MoonBridge.LI_TOUCH_EVENT_UP, pointerId, 0, 0, 0);
        }
        return touched;
    }

    /** Battery characteristic / report 0x43 without the id: [0] charge state, [1] percent. */
    private void handleBattery(byte[] data) {
        if (data == null || data.length < 2) {
            return;
        }
        int chargeState = data[0] & 0xFF;
        int percent = Math.min(100, data[1] & 0xFF);
        byte state = chargeState != 0 ? MoonBridge.LI_BATTERY_STATE_CHARGING : MoonBridge.LI_BATTERY_STATE_DISCHARGING;
        if (percent >= 100 && chargeState != 0) {
            state = MoonBridge.LI_BATTERY_STATE_FULL;
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
        LimeLog.info("Steam Controller BLE: stick extents (raw): left "
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
        try {
            SharedPreferences prefs = context.getSharedPreferences(STICK_EXTENT_PREFS, Context.MODE_PRIVATE);
            String key = device.getAddress();
            leftExtentX = Math.max(STICK_EXTENT_FLOOR, Math.min(1f, prefs.getFloat(key + ".lx", STICK_EXTENT_FLOOR)));
            leftExtentY = Math.max(STICK_EXTENT_FLOOR, Math.min(1f, prefs.getFloat(key + ".ly", STICK_EXTENT_FLOOR)));
            rightExtentX = Math.max(STICK_EXTENT_FLOOR, Math.min(1f, prefs.getFloat(key + ".rx", STICK_EXTENT_FLOOR)));
            rightExtentY = Math.max(STICK_EXTENT_FLOOR, Math.min(1f, prefs.getFloat(key + ".ry", STICK_EXTENT_FLOOR)));
        } catch (RuntimeException e) {
            LimeLog.warning("Steam Controller BLE: stick extents not loaded: " + e);
        }
    }

    private void saveStickExtents() {
        if (!extentsDirty) {
            return;
        }
        extentsDirty = false;
        try {
            String key = device.getAddress();
            context.getSharedPreferences(STICK_EXTENT_PREFS, Context.MODE_PRIVATE).edit()
                    .putFloat(key + ".lx", leftExtentX).putFloat(key + ".ly", leftExtentY)
                    .putFloat(key + ".rx", rightExtentX).putFloat(key + ".ry", rightExtentY)
                    .apply();
            LimeLog.info(String.format(Locale.ROOT, "Steam Controller BLE: stick extents saved: left %.3f/%.3f right %.3f/%.3f",
                    leftExtentX, leftExtentY, rightExtentX, rightExtentY));
        } catch (RuntimeException e) {
            LimeLog.warning("Steam Controller BLE: stick extents not saved: " + e);
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

    private static int u16(byte[] p, int i) {
        return (p[i] & 0xFF) | ((p[i + 1] & 0xFF) << 8);
    }

    private static int u32(byte[] p, int i) {
        return (p[i] & 0xFF) | ((p[i + 1] & 0xFF) << 8) | ((p[i + 2] & 0xFF) << 16) | ((p[i + 3] & 0xFF) << 24);
    }

    private static float clamp01(float v) {
        return v < 0 ? 0 : (v > 1 ? 1 : v);
    }

    /** The first 32 bits of a Valve characteristic UUID, or -1 when it is not one. */
    private static long shortUuid(UUID uuid) {
        String s = uuid.toString().toLowerCase(Locale.ROOT);
        if (!s.endsWith(VALVE_UUID_TAIL)) {
            return -1;
        }
        try {
            return Long.parseLong(s.substring(0, 8), 16);
        } catch (NumberFormatException e) {
            return -1;
        }
    }
}
