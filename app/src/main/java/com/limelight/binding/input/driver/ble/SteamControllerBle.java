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
import android.os.Build;
import android.os.Handler;
import android.os.HandlerThread;
import android.os.Process;
import android.os.SystemClock;

import com.limelight.LimeLog;
import com.limelight.binding.input.driver.SteamControllerInput;
import com.limelight.binding.input.driver.UsbDriverListener;
import com.limelight.nvstream.jni.MoonBridge;

import java.util.ArrayDeque;
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
public class SteamControllerBle extends SteamControllerInput {

    public static final int VENDOR_ID_VALVE = 0x28de;
    public static final int PRODUCT_ID_IBEX_BLE = 0x1303;

    static final UUID VALVE_SERVICE = UUID.fromString("100f6c32-1735-4313-b402-38567131e5f3");
    private static final String VALVE_UUID_TAIL = "-1735-4313-b402-38567131e5f3";
    // The same tail as the two halves of the 128-bit value: bits 31..0 of the MSB, and the LSB.
    private static final long VALVE_UUID_MSB_TAIL = 0x17354313L;
    private static final long VALVE_UUID_LSB = 0xb40238567131e5f3L;
    // Feature commands and HID output reports have distinct BLE endpoints (SDL's
    // HIDDeviceBLESteamController): 0x34 takes feature commands, B5 is output report 0x80
    // (rumble), 7A/7C carry input reports 0x45/0x47. Firmware that does not expose those
    // falls back to what the first hardware test used: the first writable characteristic in
    // B5..BE and any full-size report on the 75..7A notify range.
    private static final long FEATURE_SHORT = 0x100f6c34L;
    private static final long STATE_45_SHORT = 0x100f6c7aL;
    private static final long STATE_47_SHORT = 0x100f6c7cL;
    private static final long NOTIFY_SHORT_LOW = 0x100f6c75L;
    private static final long NOTIFY_SHORT_HIGH = 0x100f6c7cL;
    private static final long WRITE_SHORT_LOW = 0x100f6cb5L;
    private static final long WRITE_SHORT_HIGH = 0x100f6cbeL;
    private static final long BATTERY_SHORT = 0x100f6c78L;
    private static final long PING_SHORT = 0x100f6c79L;
    private static final UUID CCCD = UUID.fromString("00002902-0000-1000-8000-00805f9b34fb");
    private static final int DESIRED_MTU = 100;
    /** Below this the 45-byte state report cannot fit one notification. */
    private static final int STATE_REPORT_MTU = 48;

    // Command ids (hid-steam.c / SDL controller_constants.h)
    private static final byte ID_CLEAR_DIGITAL_MAPPINGS = (byte) 0x81;
    private static final byte ID_SET_DEFAULT_DIGITAL_MAPPINGS = (byte) 0x85;
    private static final byte ID_SET_SETTINGS_VALUES = (byte) 0x87;
    private static final byte ID_LOAD_DEFAULT_SETTINGS = (byte) 0x8E;
    private static final byte ID_TRIGGER_HAPTIC_PULSE = (byte) 0x8F;
    // Deck-era haptic engine commands (SDL controller_constants.h); the Triton's output reports
    // 0x80 and 0x82 carry the same fields, so they are what a host's report is replayed with.
    private static final byte ID_TRIGGER_HAPTIC_CMD = (byte) 0xEA;
    private static final byte ID_TRIGGER_RUMBLE_CMD = (byte) 0xEB;
    // Setting ids (index in the firmware settings enum)
    private static final int SETTING_LIZARD_MODE = 9;
    private static final int SETTING_IMU_MODE = 48;
    private static final int SETTING_STEAM_WATCHDOG_ENABLE = 71;
    private static final int IMU_MODE_RAW_ACCEL = 0x08;
    private static final int IMU_MODE_RAW_GYRO = 0x10;

    /** The firmware re-arms lizard mode when it thinks Steam is gone; keep telling it otherwise. */
    private static final long KEEPALIVE_INTERVAL_MS = 1000;
    private static final long RECONNECT_DELAY_MS = 2000;
    // Each direct reconnect scans at high duty cycle until the stack gives up (~30 s), and a
    // bonded controller that is switched back on reconnects through the system anyway
    // (the manager restarts the driver on ACL_CONNECTED). Two attempts cover a radio glitch.
    private static final int MAX_RECONNECT_ATTEMPTS = 2;
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
    /** Rumble methods (steam_controller_rumble_method preference). */
    public static final int RUMBLE_METHOD_RUMBLE_CMD = 0;
    public static final int RUMBLE_METHOD_PULSES = 1;
    /** The firmware stops a rumble command on its own after ~50 ms, so SDL re-sends every 40. */
    private static final int RUMBLE_CMD_RESEND_MS = 40;
    /** A host 0x80 that lands this soon after the last rumble write just updates the chain. */
    private static final int RUMBLE_CMD_MIN_GAP_MS = 20;
    /** A write whose completion never arrives would wedge the queue; clear it after this. */
    private static final long WRITE_WATCHDOG_MS = 2000;
    // connectGatt -> onReady: an accepted requestMtu/discoverServices/writeDescriptor whose
    // callback never arrives must not leave the link half-open for the rest of the stream.
    private static final long SETUP_WATCHDOG_MS = 10_000;
    /** Retry delay after the stack refused a write (one operation outstanding at a time). */
    private static final long WRITE_RETRY_MS = 50;
    private static final int MAX_PENDING_WRITES = 32;
    /** stop(): how long to wait for the restore writes to complete before closing anyway. */
    private static final long CLOSE_FALLBACK_MS = 400;

    private final Context context;
    private final BluetoothDevice device;
    private final int rumbleHoldMs;
    private final int rumbleMethod;
    // The driver's own thread: rumble and Steam haptic reports from the host, the write
    // queue retries, the keep-alive and the watchdog all run here, so haptic latency no
    // longer tracks main-thread load (perf overlay, toasts, layout). Quits once the link
    // is closed after stop(); an instance is never started again after that.
    private final HandlerThread handlerThread =
            new HandlerThread("SteamControllerBle", Process.THREAD_PRIORITY_URGENT_DISPLAY);
    private final Handler handler;

    private volatile BluetoothGatt gatt;
    private boolean connecting; // guarded by this; connectGatt itself runs outside the lock
    private volatile BluetoothGattCharacteristic writeChar;
    private BluetoothGattCharacteristic batteryChar;
    private final ArrayDeque<BluetoothGattCharacteristic> pendingSubscriptions = new ArrayDeque<>();
    private volatile BluetoothGattCharacteristic stateChar;
    private boolean stateSubscribed;
    private final ArrayDeque<byte[]> writeQueue = new ArrayDeque<>();
    private boolean writeBusy;
    private long writeRejectedSinceMs = -1;
    // stop() in progress: the restore commands still go out, nothing else does.
    private volatile boolean closing;
    private boolean mtuRequested;
    private boolean announced;
    // One warning per link when state reports arrive truncated (an MTU that stayed at 23).
    private int shortReportsLogged;
    /** Set after a with-response write was refused and the characteristic allows no-response. */
    private boolean forceNoResponse;
    private byte[] inFlightCmd;
    private volatile boolean stopped;
    private int reconnectAttempts;
    private boolean everConnected;

    // Armed on CONNECTED, disarmed by onReady/onLinkLost. A setup that stalls after an accepted
    // GATT operation used to keep `gatt` set forever, which also disarmed the manager's ACL
    // restart (it only restarts a driver whose link is down). Recycle the link instead.
    private final Runnable setupWatchdog = () -> {
        BluetoothGatt g = gatt;
        if (g != null && !announced && !stopped) {
            LimeLog.warning("Steam Controller BLE: setup did not complete within " + SETUP_WATCHDOG_MS + " ms; recycling the link");
            onLinkLost(g);
        }
    };
    // Rumble state; only touched on the handler thread.
    private float rumbleLow, rumbleHigh;
    private int rumbleLowRaw, rumbleHighRaw;
    // A host 0x80 rumble report being replayed instead of the plain rumble values (null = plain).
    private byte[] rumbleReport;
    private boolean rumbleActive;
    private long rumbleDeadline;
    private long lastRumbleWriteMs;

    /** The one battery read after setup. It must not act on a link it was not posted for. */
    private int batteryReadRetries;
    private final Runnable batteryRead = new Runnable() {
        @SuppressLint("MissingPermission")
        @Override
        public void run() {
            BluetoothGatt current = gatt;
            BluetoothGattCharacteristic ch = batteryChar;
            if (current == null || ch == null || stopped) {
                return;
            }
            boolean accepted = false;
            try {
                accepted = current.readCharacteristic(ch);
            } catch (SecurityException | IllegalStateException ignored) {
            }
            if (!accepted && batteryReadRetries++ < 4) {
                // Refused while a write is outstanding: try again shortly rather than
                // never reporting the level until the controller pushes it.
                handler.postDelayed(this, 250);
            }
        }
    };

    private final Runnable keepAlive = new Runnable() {
        @Override
        public void run() {
            if (stopped || gatt == null || writeChar == null) {
                return;
            }
            // The whole setup triple (11 bytes a second): a settings write lost at
            // connect time (the stack refuses a write while one is outstanding) would
            // otherwise leave the watchdog armed or the IMU off for the session.
            enqueueWrite(settings(SETTING_LIZARD_MODE, 0, SETTING_STEAM_WATCHDOG_ENABLE, 0,
                    SETTING_IMU_MODE, motionEnabled ? (IMU_MODE_RAW_ACCEL | IMU_MODE_RAW_GYRO) : 0));
            handler.postDelayed(this, KEEPALIVE_INTERVAL_MS);
        }
    };

    public SteamControllerBle(int deviceId, UsbDriverListener listener, Context context,
                              BluetoothDevice device, boolean motionEnabled, boolean splitPads, int gripsMode,
                              int rumbleHoldMs, int rumbleMethod, boolean stickRim) {
        super(deviceId, listener, PRODUCT_ID_IBEX_BLE, context, device.getAddress(), "BLE",
                motionEnabled, splitPads, gripsMode, stickRim);
        this.context = context.getApplicationContext();
        this.device = device;
        this.rumbleHoldMs = Math.max(0, rumbleHoldMs);
        this.rumbleMethod = rumbleMethod;
        handlerThread.start();
        handler = new Handler(handlerThread.getLooper());
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
        synchronized (this) {
            if (stopped) return false;
            if (connecting || gatt != null) return true;
            connecting = true;
            closing = false;
        }
        LimeLog.info("Steam Controller BLE: connecting to " + device.getAddress());
        BluetoothGatt connected = null;
        try {
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.M) {
                connected = device.connectGatt(context, false, gattCallback, BluetoothDevice.TRANSPORT_LE);
            } else {
                connected = device.connectGatt(context, false, gattCallback);
            }
        } catch (SecurityException e) {
            LimeLog.warning("Steam Controller BLE: no Bluetooth permission: " + e.getMessage());
        }
        synchronized (this) {
            connecting = false;
            if (!stopped) {
                gatt = connected;
                return connected != null;
            }
        }
        // stop() can retire the looper while connectGatt is inside the platform.
        // A late result must not publish a connection on that retired instance.
        if (connected != null) {
            try {
                connected.disconnect();
                connected.close();
            } catch (SecurityException | IllegalStateException e) {
                LimeLog.warning("Steam Controller BLE: late connection close failed: " + e.getMessage());
            }
        }
        return false;
    }

    @SuppressLint("MissingPermission")
    @Override
    public void stop() {
        BluetoothGatt g;
        boolean restore;
        synchronized (this) {
            if (stopped) return;
            stopped = true;
            // Give the controller its keyboard/mouse emulation back for the rest of the
            // system. The stack takes one GATT operation at a time, so the restore commands
            // go through the queue (behind a motors-off) and the link closes from the last
            // completion, or after a fallback delay if the link is already gone. With the
            // firmware's Steam watchdog disabled, a restore that never arrives leaves the
            // controller without keyboard/mouse until it is power-cycled.
            //
            // `closing` and the restore queue are published in the same critical section:
            // a write completion that lands while stop() is still running must find the
            // restore commands queued, or it computes "closing and drained" and closes the
            // link before the motors-off and the restores exist.
            g = gatt;
            restore = g != null && writeChar != null;
            closing = restore;
            if (restore) {
                writeQueue.clear();
                writeQueue.add(rumbleCommand(0, 0));
                writeQueue.add(new byte[]{ID_SET_DEFAULT_DIGITAL_MAPPINGS, 0});
                writeQueue.add(new byte[]{ID_LOAD_DEFAULT_SETTINGS, 0});
            }
        }
        handler.removeCallbacksAndMessages(null);
        saveStickExtents();
        if (restore) {
            flushWrites();
            handler.postDelayed(closeLink, CLOSE_FALLBACK_MS);
        } else {
            closeLink.run();
        }
        if (announced) {
            announced = false;
            notifyDeviceRemoved();
        }
    }

    /** Drops the link once the restore writes are done (or given up on). */
    private final Runnable closeLink = new Runnable() {
        @SuppressLint("MissingPermission")
        @Override
        public void run() {
            handler.removeCallbacks(this);
            BluetoothGatt g = gatt;
            gatt = null;
            try {
                if (g != null) {
                    g.disconnect();
                    g.close();
                }
            } catch (SecurityException | IllegalStateException e) {
                LimeLog.warning("Steam Controller BLE: close failed: " + e.getMessage());
            }
            if (stopped) {
                // Last use of the thread: the restore writes are done (or given up on).
                handlerThread.quitSafely();
            }
        }
    };

    // ----- GATT -----

    private final BluetoothGattCallback gattCallback = new BluetoothGattCallback() {
        @SuppressLint("MissingPermission")
        @Override
        public void onConnectionStateChange(BluetoothGatt g, int status, int newState) {
            if (stopped || g != gatt) {
                return;
            }
            if (newState == BluetoothProfile.STATE_CONNECTED) {
                if (status != BluetoothGatt.GATT_SUCCESS) {
                    // Some stacks report a failed connection attempt as CONNECTED with an error
                    // status; nothing follows it, so treat it as a lost link (which reconnects).
                    LimeLog.warning("Steam Controller BLE: connected with status " + status + "; recycling the link");
                    onLinkLost(g);
                    return;
                }
                LimeLog.info("Steam Controller BLE: connected, negotiating");
                everConnected = true;
                mtuRequested = true;
                handler.removeCallbacks(setupWatchdog);
                handler.postDelayed(setupWatchdog, SETUP_WATCHDOG_MS);
                g.requestConnectionPriority(BluetoothGatt.CONNECTION_PRIORITY_HIGH);
                if (!g.requestMtu(DESIRED_MTU)) {
                    mtuRequested = false;
                    if (!g.discoverServices()) onLinkLost(g);
                }
            } else if (newState == BluetoothProfile.STATE_DISCONNECTED) {
                LimeLog.info("Steam Controller BLE: disconnected (status " + status + ")");
                onLinkLost(g);
            }
        }

        @SuppressLint("MissingPermission")
        @Override
        public void onMtuChanged(BluetoothGatt g, int mtu, int status) {
            if (stopped || g != gatt) {
                return;
            }
            // The stack may report this twice; discover services once.
            if (mtuRequested) {
                mtuRequested = false;
                LimeLog.info("Steam Controller BLE: MTU " + mtu);
                if (mtu < STATE_REPORT_MTU) {
                    // State reports are 45 bytes: a 23-byte MTU truncates every one of
                    // them and the controller would look connected but stay silent.
                    LimeLog.warning("Steam Controller BLE: MTU " + mtu + " is too small for state reports");
                }
                if (!g.discoverServices()) onLinkLost(g);
            }
        }

        @SuppressLint("MissingPermission")
        @Override
        public void onServicesDiscovered(BluetoothGatt g, int status) {
            if (stopped || g != gatt) {
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
                stateSubscribed = false;
                stateChar = null;
                writeChar = null;
                batteryChar = null;
                BluetoothGattCharacteristic legacyWrite = null;
                BluetoothGattCharacteristic previousState = null;
                ArrayDeque<BluetoothGattCharacteristic> otherNotify = new ArrayDeque<>();
                for (BluetoothGattCharacteristic ch : service.getCharacteristics()) {
                    long shortId = shortUuid(ch.getUuid());
                    int props = ch.getProperties();
                    boolean notify = (props & BluetoothGattCharacteristic.PROPERTY_NOTIFY) != 0;
                    boolean writable = (props & (BluetoothGattCharacteristic.PROPERTY_WRITE
                            | BluetoothGattCharacteristic.PROPERTY_WRITE_NO_RESPONSE)) != 0;
                    if ((shortId == STATE_45_SHORT || shortId == STATE_47_SHORT) && notify
                            && (stateChar == null || shortId == STATE_47_SHORT)) {
                        // Prefer the timestamped layout, independently of discovery order.
                        previousState = stateChar;
                        stateChar = ch;
                    } else if (notify && shortId >= NOTIFY_SHORT_LOW && shortId <= NOTIFY_SHORT_HIGH) {
                        otherNotify.add(ch);
                    }
                    if (shortId == BATTERY_SHORT) {
                        batteryChar = ch;
                    }
                    if (shortId == FEATURE_SHORT && writable) {
                        writeChar = ch;
                    } else if (legacyWrite == null && writable
                            && shortId >= WRITE_SHORT_LOW && shortId <= WRITE_SHORT_HIGH) {
                        legacyWrite = ch;
                    }
                }
                if (previousState != null) {
                    otherNotify.add(previousState);
                }
                if (writeChar == null && legacyWrite != null) {
                    LimeLog.warning("Steam Controller BLE: feature characteristic 0x" + Long.toHexString(FEATURE_SHORT)
                            + " not offered; commands go to " + legacyWrite.getUuid());
                    writeChar = legacyWrite;
                }
                if (stateChar == null && !otherNotify.isEmpty()) {
                    LimeLog.warning("Steam Controller BLE: no 0x45/0x47 input characteristic; decoding full-size reports from any notify characteristic");
                }
                // The selected state source is subscribed first, then battery, then the rest of
                // the range: the extra characteristics only carry acknowledgements/pings, which
                // the short-report log keeps visible for hardware diagnostics.
                if (stateChar != null) {
                    pendingSubscriptions.add(stateChar);
                }
                if (batteryChar != null && (batteryChar.getProperties()
                        & BluetoothGattCharacteristic.PROPERTY_NOTIFY) != 0) {
                    otherNotify.remove(batteryChar);
                    pendingSubscriptions.add(batteryChar);
                }
                pendingSubscriptions.addAll(otherNotify);
            }
            LimeLog.info("Steam Controller BLE: " + pendingSubscriptions.size() + " notify characteristics, command characteristic "
                    + (writeChar != null ? writeChar.getUuid() + " (properties 0x" + Integer.toHexString(writeChar.getProperties()) + ")" : "none"));
            LimeLog.info("Steam Controller BLE: state characteristic "
                    + (stateChar != null ? stateChar.getUuid() : "none (legacy range)"));
            boolean anyStateSource;
            synchronized (SteamControllerBle.this) {
                anyStateSource = stateChar != null;
                for (BluetoothGattCharacteristic ch : pendingSubscriptions) {
                    anyStateSource |= carriesState(ch);
                }
            }
            if (writeChar == null || !anyStateSource) {
                onLinkLost(g);
                return;
            }
            if (!subscribeNext(g)) {
                onReady(g);
            }
        }

        @Override
        public void onDescriptorWrite(BluetoothGatt g, BluetoothGattDescriptor descriptor, int status) {
            if (stopped || g != gatt) {
                return;
            }
            if (status != BluetoothGatt.GATT_SUCCESS) {
                // Optional battery failure is harmless; onReady requires state success.
                LimeLog.warning("Steam Controller BLE: subscribing to " + descriptor.getCharacteristic().getUuid()
                        + " failed, status " + status);
            } else if (carriesState(descriptor.getCharacteristic())) {
                stateSubscribed = true;
            }
            if (!subscribeNext(g)) {
                onReady(g);
            }
        }

        @Override
        public void onCharacteristicWrite(BluetoothGatt g, BluetoothGattCharacteristic ch, int status) {
            // A closed connection may still deliver its final callback after a reconnect.
            if (g != gatt) {
                return;
            }
            if (stopped && !closing) {
                // Not restoring (no link to restore): release the write slot anyway
                synchronized (SteamControllerBle.this) {
                    writeBusy = false;
                }
                return;
            }
            byte[] failed;
            boolean withResponse;
            synchronized (SteamControllerBle.this) {
                failed = inFlightCmd;
                inFlightCmd = null;
                withResponse = !forceNoResponse;
            }
            boolean retryWithoutResponse = false;
            if (status != BluetoothGatt.GATT_SUCCESS) {
                LimeLog.warning("Steam Controller BLE: write failed, status " + status + describeCommand(failed)
                        + (withResponse ? " (with response)" : " (without response)"));
                // Some stacks/firmware refuse a write request on a characteristic that only
                // really serves write-without-response: switch this link over and re-send.
                if (withResponse && failed != null
                        && (ch.getProperties() & BluetoothGattCharacteristic.PROPERTY_WRITE_NO_RESPONSE) != 0) {
                    LimeLog.warning("Steam Controller BLE: switching to write-without-response for this link");
                    retryWithoutResponse = true;
                }
            }
            handler.removeCallbacks(writeWatchdog);
            boolean drained;
            synchronized (SteamControllerBle.this) {
                writeBusy = false;
                if (retryWithoutResponse && g == gatt) {
                    forceNoResponse = true;
                    writeQueue.addFirst(failed);
                }
                drained = closing && writeQueue.isEmpty();
            }
            if (drained) {
                handler.post(closeLink);
                return;
            }
            flushWrites();
        }

        @Override
        public void onCharacteristicRead(BluetoothGatt g, BluetoothGattCharacteristic ch, int status) {
            if (stopped || g != gatt) {
                return;
            }
            if (status == BluetoothGatt.GATT_SUCCESS && shortUuid(ch.getUuid()) == BATTERY_SHORT) {
                handleBattery(ch.getValue());
            }
        }

        @Override
        public void onCharacteristicChanged(BluetoothGatt g, BluetoothGattCharacteristic ch) {
            if (stopped || g != gatt) {
                return;
            }
            byte[] data = ch.getValue();
            if (data == null) {
                return;
            }
            long shortId = shortUuid(ch.getUuid());
            if (shortId == BATTERY_SHORT) {
                handleBattery(data);
            } else if (shortId == PING_SHORT) {
                // ack/ping-pong tied to our writes; nothing to do
            } else if (data.length >= 45 && carriesState(ch)) {
                // Notifications omit the HID report ID. Both layouts are 45 bytes:
                // 0x47 adds a pad timestamp and shortens the IMU timestamp by two bytes.
                handleState(data, 0, shortId == STATE_47_SHORT);
            } else if (shortReportsLogged < 5) {
                // Unexpected characteristic or truncated state. Log the first few
                // reports with their source for hardware diagnostics.
                shortReportsLogged++;
                LimeLog.warning("Steam Controller BLE: " + data.length + "-byte report on 0x" + Long.toHexString(shortId)
                        + " dropped: " + hex(data));
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
        if (!g.setCharacteristicNotification(ch, true)) {
            onLinkLost(g);
            return true; // Recovery owns the next step; do not announce this link as ready.
        }
        BluetoothGattDescriptor cccd = ch.getDescriptor(CCCD);
        if (cccd == null) {
            // Nothing to wait for; move on synchronously
            if (carriesState(ch)) {
                stateSubscribed = true;
            }
            return subscribeNext(g);
        }
        cccd.setValue(BluetoothGattDescriptor.ENABLE_NOTIFICATION_VALUE);
        if (!g.writeDescriptor(cccd)) {
            // A rejected operation has no completion callback. Recover the link instead
            // of leaving the rest of setup waiting indefinitely.
            onLinkLost(g);
        }
        return true;
    }

    /**
     * Whether input state is decoded from this characteristic: the selected 0x45/0x47 source
     * when the firmware offers one, otherwise (legacy range) any notify characteristic other
     * than battery and ping, as the first hardware test decoded it.
     */
    private boolean carriesState(BluetoothGattCharacteristic ch) {
        BluetoothGattCharacteristic selected = stateChar;
        if (selected != null) {
            return ch == selected;
        }
        long shortId = shortUuid(ch.getUuid());
        return shortId >= NOTIFY_SHORT_LOW && shortId <= NOTIFY_SHORT_HIGH
                && shortId != BATTERY_SHORT && shortId != PING_SHORT;
    }

    /** All notifications are on: take the controller out of lizard mode and announce it. */
    @SuppressLint("MissingPermission")
    private void onReady(BluetoothGatt g) {
        if (stopped || g != gatt) {
            return;
        }
        if (!stateSubscribed) {
            // A battery subscription alone cannot deliver gamepad input.
            LimeLog.warning("Steam Controller BLE: state notification subscription failed");
            onLinkLost(g);
            return;
        }
        reconnectAttempts = 0;
        handler.removeCallbacks(setupWatchdog);
        enqueueWrite(new byte[]{ID_CLEAR_DIGITAL_MAPPINGS, 0});
        // All settings in one command: keep-alive deduplication must not discard the IMU setup.
        enqueueWrite(settings(SETTING_LIZARD_MODE, 0, SETTING_STEAM_WATCHDOG_ENABLE, 0,
                SETTING_IMU_MODE, motionEnabled ? (IMU_MODE_RAW_ACCEL | IMU_MODE_RAW_GYRO) : 0));
        if (batteryChar != null) {
            // After the setup writes have had their turn: a read is refused while a write
            // is outstanding and would just be lost.
            batteryReadRetries = 0;
            handler.removeCallbacks(batteryRead);
            handler.postDelayed(batteryRead, 500);
        }
        handler.removeCallbacks(keepAlive);
        handler.postDelayed(keepAlive, KEEPALIVE_INTERVAL_MS);
        if (!announced) {
            announced = true;
            LimeLog.info("Steam Controller BLE: ready, reporting as LI_CTYPE_STEAM");
            notifyDeviceAdded();
            // The listener now owns a fresh context. States received during setup,
            // or delivered to the previous connection, cannot satisfy this one.
            reportedStateValid = false;
        }
    }

    @SuppressLint("MissingPermission")
    private void onLinkLost(BluetoothGatt g) {
        if (g != gatt) {
            return;
        }
        handler.removeCallbacks(keepAlive);
        handler.removeCallbacks(rumbleRefresh);
        handler.removeCallbacks(retryFlush);
        handler.removeCallbacks(writeWatchdog);
        handler.removeCallbacks(batteryRead);
        handler.removeCallbacks(setupWatchdog);
        synchronized (this) {
            writeQueue.clear();
            writeBusy = false;
            writeRejectedSinceMs = -1;
            writeChar = null;
            batteryChar = null;
            pendingSubscriptions.clear();
            stateSubscribed = false;
            stateChar = null;
            rumbleActive = false;
            rumbleReport = null;
            shortReportsLogged = 0;
            forceNoResponse = false;
            inFlightCmd = null;
        }
        if (announced) {
            announced = false;
            // Release any touches the host still thinks are down
            releaseTouches();
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
    private static String describeCommand(byte[] cmd) {
        if (cmd == null || cmd.length == 0) {
            return "";
        }
        return " (cmd 0x" + Integer.toHexString(cmd[0] & 0xFF) + ", " + cmd.length + " bytes)";
    }

    private static String hex(byte[] data) {
        StringBuilder sb = new StringBuilder(data.length * 3);
        for (int i = 0; i < data.length && i < 32; i++) {
            if (i > 0) sb.append(' ');
            sb.append(Character.forDigit((data[i] >> 4) & 0xF, 16)).append(Character.forDigit(data[i] & 0xF, 16));
        }
        if (data.length > 32) sb.append(" ...");
        return sb.toString();
    }

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

    /** A motors-off: a zero rumble command or a zero-count pulse. Never evicted from the queue. */
    private static boolean isStop(byte[] cmd) {
        if (cmd.length >= 9 && cmd[0] == ID_TRIGGER_RUMBLE_CMD) {
            return cmd[5] == 0 && cmd[6] == 0 && cmd[7] == 0 && cmd[8] == 0;
        }
        if (cmd.length >= 9 && cmd[0] == ID_TRIGGER_HAPTIC_PULSE) {
            // A zero on-duration is also silent, even when the repeat count is nonzero.
            // Keep the same stop semantics as the driver, host and native callback queue.
            return (cmd[3] == 0 && cmd[4] == 0) || (cmd[7] == 0 && cmd[8] == 0);
        }
        if (cmd.length >= 4 && cmd[0] == ID_TRIGGER_HAPTIC_CMD) {
            return cmd[3] == 0;
        }
        return false;
    }

    private static boolean sameStopTarget(byte[] a, byte[] b) {
        return a[0] == b[0] && (a[0] == ID_TRIGGER_RUMBLE_CMD || a[2] == b[2]);
    }

    private void enqueueWrite(byte[] cmd) {
        synchronized (this) {
            if (closing || stopped || gatt == null || writeChar == null) {
                return;
            }
            if (isStop(cmd)) {
                // The newest stop supersedes older stops for the same actuator, and
                // stays after any intervening effects. Never accumulate an unbounded
                // queue of "protected" stop commands while GATT is stalled.
                writeQueue.removeIf(queued -> isStop(queued) && sameStopTarget(queued, cmd));
            }
            if (cmd[0] == ID_SET_SETTINGS_VALUES) {
                // One keep-alive waiting is enough; a stalled link must not fill up with them.
                for (byte[] queued : writeQueue) {
                    if (queued[0] == ID_SET_SETTINGS_VALUES) {
                        return;
                    }
                }
            }
            if (writeQueue.size() >= MAX_PENDING_WRITES) {
                // Drop the oldest command that is not a motors-off, so a stall cannot
                // leave a pad buzzing by evicting the stop and keeping the start.
                byte[] victim = null;
                for (byte[] queued : writeQueue) {
                    if (!isStop(queued)) {
                        victim = queued;
                        break;
                    }
                }
                if (victim != null) {
                    writeQueue.remove(victim);
                } else {
                    return;
                }
            }
            writeQueue.add(cmd);
        }
        flushWrites();
    }

    /** A timed-out operation cannot be distinguished from a later write's callback. */
    private final Runnable writeWatchdog = () -> {
        BluetoothGatt expired;
        synchronized (this) {
            if (!writeBusy) {
                return;
            }
            expired = gatt;
        }
        LimeLog.warning("Steam Controller BLE: a write never completed; recycling the link");
        if (closing) closeLink.run();
        else if (expired != null) onLinkLost(expired);
    };

    @SuppressLint("MissingPermission")
    private void flushWrites() {
        BluetoothGatt g;
        BluetoothGattCharacteristic ch;
        byte[] cmd;
        synchronized (this) {
            g = gatt;
            ch = writeChar;
            if (writeBusy || g == null || ch == null || (stopped && !closing)) {
                return;
            }
            cmd = writeQueue.poll();
            if (cmd == null) {
                return;
            }
            writeBusy = true;
        }
        boolean accepted = false;
        try {
            // Haptics are fire-and-forget and frequent (40 ms rumble re-sends, 100 ms clicks):
            // without a response each costs no round trip on the link. The stack still
            // reports the write complete, so the one-at-a-time pacing holds either way.
            boolean haptic = cmd[0] == ID_TRIGGER_RUMBLE_CMD || cmd[0] == ID_TRIGGER_HAPTIC_CMD
                    || cmd[0] == ID_TRIGGER_HAPTIC_PULSE;
            boolean noResponse = (haptic || forceNoResponse)
                    && (ch.getProperties() & BluetoothGattCharacteristic.PROPERTY_WRITE_NO_RESPONSE) != 0;
            ch.setWriteType(noResponse ? BluetoothGattCharacteristic.WRITE_TYPE_NO_RESPONSE
                    : BluetoothGattCharacteristic.WRITE_TYPE_DEFAULT);
            ch.setValue(cmd);
            accepted = g.writeCharacteristic(ch);
        } catch (SecurityException | IllegalStateException ignored) {
        }
        if (accepted) {
            synchronized (this) {
                if (g != gatt || ch != writeChar) return;
                inFlightCmd = cmd;
                writeRejectedSinceMs = -1;
                handler.removeCallbacks(writeWatchdog);
                handler.postDelayed(writeWatchdog, WRITE_WATCHDOG_MS);
            }
            return;
        }
        // Refused: another GATT operation is outstanding (a read, a descriptor write) or
        // the link is going down. Put the command back at the head and try again shortly.
        boolean expired;
        synchronized (this) {
            // A failed write may race link loss. Never put its command into a
            // replacement connection's queue or clear that connection's busy flag.
            if (g != gatt || ch != writeChar || (stopped && !closing)) return;
            writeBusy = false;
            long now = SystemClock.uptimeMillis();
            if (writeRejectedSinceMs < 0) writeRejectedSinceMs = now;
            expired = now - writeRejectedSinceMs >= WRITE_WATCHDOG_MS;
            writeQueue.addFirst(cmd);
        }
        handler.removeCallbacks(retryFlush);
        if (expired) {
            LimeLog.warning("Steam Controller BLE: writes keep being rejected; recycling the link");
            if (closing) closeLink.run();
            else onLinkLost(g);
        } else {
            handler.postDelayed(retryFlush, WRITE_RETRY_MS);
        }
    }

    private final Runnable retryFlush = this::flushWrites;

    @Override
    public void rumble(short lowFreqMotor, short highFreqMotor) {
        final int lowRaw = lowFreqMotor & 0xFFFF, highRaw = highFreqMotor & 0xFFFF;
        final float low = lowRaw / 65535f;
        final float high = highRaw / 65535f;
        handler.post(() -> {
            if (stopped || gatt == null || writeChar == null) {
                return;
            }
            rumbleLow = low;
            rumbleHigh = high;
            rumbleLowRaw = lowRaw;
            rumbleHighRaw = highRaw;
            handler.removeCallbacks(rumbleRefresh);
            if (low <= 0.01f && high <= 0.01f) {
                if (rumbleActive) {
                    rumbleActive = false;
                    stopRumble();   // stops whatever was driving, a replayed report included
                }
                rumbleReport = null;
                return;
            }
            rumbleReport = null;
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
            if (!rumbleActive || stopped || gatt == null || writeChar == null) {
                return;
            }
            long now = SystemClock.uptimeMillis();
            if (now >= rumbleDeadline) {
                rumbleActive = false;
                stopRumble();
                return;
            }
            long remaining = Math.max(1, rumbleDeadline - now);
            if (rumbleReport != null) {
                // A host rumble report, re-issued the way Steam and SDL keep the firmware's
                // ~50 ms safety timeout from cutting it off.
                lastRumbleWriteMs = now;
                enqueueWrite(rumbleCommandFromReport(rumbleReport));
                handler.postDelayed(this, Math.min(RUMBLE_CMD_RESEND_MS, remaining));
            } else if (rumbleMethod == RUMBLE_METHOD_PULSES) {
                // Bound the train to the hold window so the last one ends on time by itself.
                int trainMs = (int) Math.min(RUMBLE_TRAIN_MS, remaining);
                int repeat = Math.max(1, trainMs * 1000 / RUMBLE_PERIOD_US);
                enqueueWrite(hapticPulse(SIDE_LEFT, rumbleLow, repeat));
                enqueueWrite(hapticPulse(SIDE_RIGHT, rumbleHigh, repeat));
                handler.postDelayed(this, Math.min(RUMBLE_REFRESH_MS, remaining));
            } else {
                lastRumbleWriteMs = now;
                enqueueWrite(rumbleCommand(rumbleLowRaw, rumbleHighRaw));
                handler.postDelayed(this, Math.min(RUMBLE_CMD_RESEND_MS, remaining));
            }
        }
    };

    /** Motors off, whichever way they were driven (a zero rumble command is also a stop). */
    private void stopRumble() {
        if (rumbleMethod == RUMBLE_METHOD_PULSES && rumbleReport == null) {
            enqueueWrite(hapticPulse(SIDE_LEFT, 0f, 0));
            enqueueWrite(hapticPulse(SIDE_RIGHT, 0f, 0));
        } else {
            enqueueWrite(rumbleCommand(0, 0));
        }
        rumbleReport = null;
    }

    /**
     * A host's Steam Controller haptic output report, replayed with the firmware's feature
     * messages: 0x80 rumble as ID_TRIGGER_RUMBLE_CMD (re-sent until the host's zero or the hold
     * limit), 0x81 pulse as ID_TRIGGER_HAPTIC_PULSE, 0x82 command as ID_TRIGGER_HAPTIC_CMD. The
     * LFO tone, log sweep and script reports have no feature-message form known to this driver.
     */
    @Override
    public void steamHaptic(byte[] report) {
        if (report == null || report.length < 2) {
            return;
        }
        final byte[] copy = report.clone();
        handler.post(() -> {
            if (stopped || gatt == null || writeChar == null) {
                return;
            }
            switch (copy[0] & 0xFF) {
                case 0x80: {
                    if (copy.length < 10) {
                        return;
                    }
                    handler.removeCallbacks(rumbleRefresh);
                    boolean silent = u16(copy, 4) == 0 && u16(copy, 7) == 0;
                    if (silent) {
                        if (rumbleActive) {
                            rumbleActive = false;
                            stopRumble();
                        }
                        rumbleReport = null;
                        return;
                    }
                    long now = SystemClock.uptimeMillis();
                    boolean chainRunning = rumbleActive && rumbleReport != null;
                    rumbleReport = copy;
                    rumbleDeadline = rumbleHoldMs == RUMBLE_HOLD_UNLIMITED
                            ? Long.MAX_VALUE : now + rumbleHoldMs;
                    rumbleActive = true;
                    if (chainRunning && now - lastRumbleWriteMs < RUMBLE_CMD_MIN_GAP_MS) {
                        // Steam re-sends every 40-50 ms itself; writing on every one of those
                        // on top of our own 40 ms chain would double the link traffic. The
                        // chain picks the new values up on its next write.
                        handler.postDelayed(rumbleRefresh, Math.max(1, RUMBLE_CMD_RESEND_MS - (now - lastRumbleWriteMs)));
                    } else {
                        rumbleRefresh.run();
                    }
                    break;
                }
                case 0x81: {
                    if (copy.length < 8 || (copy[1] & 0xFF) > 2) {
                        return;
                    }
                    // side, on_us, off_us, repeat; a zero-repeat pulse is Steam's stop for that side
                    enqueueWrite(firePulse(copy[1], u16(copy, 2), u16(copy, 4), u16(copy, 6)));
                    break;
                }
                case 0x82: {
                    if (copy.length < 4 || (copy[1] & 0xFF) > 2) {
                        return;
                    }
                    enqueueWrite(triggerHaptic(copy[1], copy[2], copy[3]));
                    break;
                }
                default:
                    break;
            }
        });
    }

    /** ID_TRIGGER_RUMBLE_CMD: MsgSimpleRumbleCmd {type, intensity, left speed, right speed, left gain, right gain}. */
    private static byte[] rumbleCommand(int leftSpeed, int rightSpeed) {
        return new byte[]{
                ID_TRIGGER_RUMBLE_CMD, 9,
                0,                                  // type
                0, 0,                               // intensity
                (byte) leftSpeed, (byte) (leftSpeed >> 8),
                (byte) rightSpeed, (byte) (rightSpeed >> 8),
                0, 0};                              // gains (dB)
    }

    /** The same message built from a 0x80 report {type, intensity, left{speed, gain}, right{speed, gain}}. */
    private static byte[] rumbleCommandFromReport(byte[] r) {
        return new byte[]{
                ID_TRIGGER_RUMBLE_CMD, 9,
                r[1],                               // type
                r[2], r[3],                         // intensity
                r[4], r[5],                         // left speed
                r[7], r[8],                         // right speed
                r[6], r[9]};                        // left gain, right gain
    }

    /** ID_TRIGGER_HAPTIC_CMD: MsgTriggerHaptic from a 0x82 report {side, command, gain_db}. */
    private static byte[] triggerHaptic(byte side, byte command, byte gainDb) {
        // The output report numbers the sides 1 = left, 0 = right; the message wants a mask.
        byte sideMask = side == 1 ? (byte) 0x01 : side == 0 ? (byte) 0x02 : (byte) 0x03;
        byte[] msg = new byte[2 + 19];
        msg[0] = ID_TRIGGER_HAPTIC_CMD;
        msg[1] = 19;
        msg[2] = sideMask;
        msg[3] = command;        // 0 off, 1 tick, 2 click, 3 tone, 4 rumble, 5 noise, 6 script, 7 sweep
        msg[4] = 0;              // ui_intensity: default
        msg[5] = gainDb;
        // freq, dur_ms, noise_intensity, lfo_freq, lfo_depth, rand_tone_gain, script_id,
        // lss_start_freq, lss_end_freq: a click or tick needs none of them
        return msg;
    }

    /**
     * ID_TRIGGER_HAPTIC_PULSE as the Linux hid-steam driver frames it for this firmware:
     * {0x8F, length 8, pad, duration u16, interval u16, count u16, gain u8 (dB, -24..+6)}.
     * The first BLE builds sent it without the length byte, which the firmware cannot parse.
     */
    private static byte[] firePulse(byte pad, int durationUs, int intervalUs, int count) {
        return new byte[]{
                ID_TRIGGER_HAPTIC_PULSE, 8, pad,
                (byte) durationUs, (byte) (durationUs >> 8),
                (byte) intervalUs, (byte) (intervalUs >> 8),
                (byte) count, (byte) (count >> 8),
                0};                                 // gain (dB)
    }

    /** Pulse side ids; the firmware numbers them the other way round from the kernel's pad ids. */
    private static final byte SIDE_RIGHT = 0;
    private static final byte SIDE_LEFT = 1;

    /** A pulse train of the given duty cycle on one pad (repeat 0 = stop the running train). */
    private static byte[] hapticPulse(byte side, float magnitude, int repeat) {
        int onUs = 0, offUs = 0;
        if (magnitude > 0.01f && repeat > 0) {
            float duty = 0.25f + 0.72f * Math.min(1f, magnitude);
            onUs = (int) (RUMBLE_PERIOD_US * duty);
            offUs = RUMBLE_PERIOD_US - onUs;
        } else {
            repeat = 0;
        }
        return firePulse(side, onUs, offUs, repeat);
    }

    @Override
    public void rumbleTriggers(short leftTrigger, short rightTrigger) {
        // No trigger motors on this controller
    }

    /** The first 32 bits of a Valve characteristic UUID, or -1 when it is not one. */
    private static long shortUuid(UUID uuid) {
        // Runs for every notification (250/s): compare the 128-bit value directly rather
        // than formatting and parsing a string per report.
        long msb = uuid.getMostSignificantBits();
        if ((msb & 0xFFFFFFFFL) != VALVE_UUID_MSB_TAIL || uuid.getLeastSignificantBits() != VALVE_UUID_LSB) {
            return -1;
        }
        return msb >>> 32;
    }
}
