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
import java.util.Arrays;
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
    // Feature commands and HID reports have distinct BLE endpoints (SDL's
    // HIDDeviceBLESteamController): 0x34 takes feature commands, each input report has its own
    // notify characteristic at 0x75 + (id - 0x40) (7A = 0x45 state, 7C = 0x47 state, 78 = 0x43
    // battery, 79 = 0x44 haptic acknowledgement), and each HID output report its own writable
    // characteristic at 0xB5 + (id - 0x80) (B5 = 0x80 rumble, B6 = 0x81 pulse, B7 = 0x82 haptic
    // command, ... BE = 0x89), without the report id, as the notifications omit theirs. Firmware
    // that does not expose those falls back to what the first hardware test used: the first
    // writable characteristic in B5..BE and any full-size report on the 75..7A notify range.
    private static final long FEATURE_SHORT = 0x100f6c34L;
    private static final long STATE_45_SHORT = 0x100f6c7aL;
    private static final long STATE_47_SHORT = 0x100f6c7cL;
    private static final long NOTIFY_SHORT_LOW = 0x100f6c75L;
    private static final long NOTIFY_SHORT_HIGH = 0x100f6c7cL;
    private static final long WRITE_SHORT_LOW = 0x100f6cb5L;
    private static final long WRITE_SHORT_HIGH = 0x100f6cbeL;
    private static final long BATTERY_SHORT = 0x100f6c78L;
    private static final long PING_SHORT = 0x100f6c79L;
    /** HID output reports 0x80..0x89, one characteristic each from WRITE_SHORT_LOW up. */
    private static final int OUTPUT_REPORT_FIRST = 0x80;
    private static final int OUTPUT_REPORT_COUNT = (int) (WRITE_SHORT_HIGH - WRITE_SHORT_LOW + 1);
    private static final byte REPORT_RUMBLE = (byte) 0x80;
    private static final byte REPORT_PULSE = (byte) 0x81;
    /** Output report framing on the air: payload only (as the notifications), or with the id. */
    private static final int OUTPUT_FRAMING_PAYLOAD = 0;
    private static final int OUTPUT_FRAMING_WITH_ID = 1;
    private static final int OUTPUT_FRAMING_UNUSABLE = 2;
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
    private static final int SETTING_WIRELESS_PACKET_VERSION = 49;
    private static final int SETTING_LEFT_TRACKPAD_CLICK_PRESSURE = 52;
    private static final int SETTING_RIGHT_TRACKPAD_CLICK_PRESSURE = 53;
    /**
     * Setting ids a host's write never reaches the controller with: the three this link sets
     * itself (lizard mode, watchdog, IMU mode; the keep-alive would fight the host over them),
     * the wireless packet version (it changes the state report this driver parses), and the
     * pad click pressures (Steam sets them to 0xFFFF over USB so the firmware never reports a
     * click and Steam derives it from pressure; here the click bits are what the host gets).
     */
    private static final int[] HOST_SETTINGS_RESERVED = {SETTING_LIZARD_MODE, SETTING_IMU_MODE,
            SETTING_STEAM_WATCHDOG_ENABLE, SETTING_WIRELESS_PACKET_VERSION,
            SETTING_LEFT_TRACKPAD_CLICK_PRESSURE, SETTING_RIGHT_TRACKPAD_CLICK_PRESSURE};
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
    /** Haptic path preference: output reports go without their id (default) or with it from the first write. */
    public static final int HAPTIC_PATH_OUTPUT = 0;
    public static final int HAPTIC_PATH_OUTPUT_WITH_ID = 1;
    /** Haptic gain preference: the host's 0x82 gain byte as sent, the wired unit's values, or +6 dB. */
    public static final int HAPTIC_GAIN_HOST = 0;
    public static final int HAPTIC_GAIN_USB = 1;
    public static final int HAPTIC_GAIN_PLUS6 = 2;
    /** What Steam sends the wired unit (libvirtualgamepad captures): ticks at -3 dB, clicks at -14 dB. */
    private static final byte USB_TICK_GAIN_DB = (byte) 0xFD;
    private static final byte USB_CLICK_GAIN_DB = (byte) 0xF2;
    private static final byte PLUS6_GAIN_DB = 6;
    private static final int HAPTIC_CMD_TICK = 1;
    private static final int HAPTIC_CMD_CLICK = 2;
    /** The firmware stops a rumble command on its own after ~50 ms, so SDL re-sends every 40. */
    private static final int RUMBLE_CMD_RESEND_MS = 40;
    /** A host 0x80 that lands this soon after the last rumble write just updates the chain. */
    private static final int RUMBLE_CMD_MIN_GAP_MS = 20;
    /** A write whose completion never arrives would wedge the queue; clear it after this. */
    private static final long WRITE_WATCHDOG_MS = 2000;
    // connectGatt -> onReady: an accepted requestMtu/discoverServices/writeDescriptor whose
    // callback never arrives must not leave the link half-open for the rest of the stream.
    private static final long SETUP_WATCHDOG_MS = 10_000;
    // connectGatt -> CONNECTED: with autoConnect off the stack reports status 133 after ~30 s.
    // An attempt that never reports anything must not keep isLinkUp() true for the stream.
    private static final long CONNECT_WATCHDOG_MS = 40_000;
    /** Retry delay after the stack refused a write (one operation outstanding at a time). */
    private static final long WRITE_RETRY_MS = 50;
    private static final int MAX_PENDING_WRITES = 32;
    /** stop(): how long to wait for the restore writes to complete before closing anyway. */
    private static final long CLOSE_FALLBACK_MS = 400;

    private final Context context;
    private final BluetoothDevice device;
    private final int rumbleHoldMs;
    private final int rumbleMethod;
    private final int hapticPath, hapticGain;
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
    // The output report characteristics by (id - 0x80); guarded by this.
    private final BluetoothGattCharacteristic[] outputChars = new BluetoothGattCharacteristic[OUTPUT_REPORT_COUNT];
    // Per link and report id: how that output report is framed, learned from the first refusal.
    private final int[] outputFraming = new int[OUTPUT_REPORT_COUNT];
    // Per link and report id: the first write went with a response, so the firmware's verdict
    // on that report is in the log (haptics otherwise go without response and cannot be refused).
    private final boolean[] outputProbed = new boolean[OUTPUT_REPORT_COUNT];
    // Haptic diagnostics for the 10 s summary line; guarded by this.
    private final int[] hostReports = new int[OUTPUT_REPORT_COUNT];
    private final int[] writtenReports = new int[OUTPUT_REPORT_COUNT];
    private int hostStops, hostIgnored, evictedWrites, hapticAcks, hostSamplesLogged, hostReportsSeen, hostGainOverrides;
    // Per report id: how many of the host's reports of that id were logged one by one.
    private final int[] hostSamplesById = new int[OUTPUT_REPORT_COUNT];
    private int hostSamplesOther;
    private byte[] lastAck;
    private long firstHostReportMs;
    /**
     * How many of the host's haptic reports are logged one by one, per report id, for the record
     * of what Steam sends: a 24-report total hid every 0x82 behind the 0x81 click pairs.
     */
    private static final int HOST_SAMPLES_PER_ID = 12;
    private static final long HAPTIC_STATS_INTERVAL_MS = 10_000;
    private final ArrayDeque<Pending> writeQueue = new ArrayDeque<>();
    private boolean writeBusy;
    private long writeAttempt;
    private long writeRejectedSinceMs = -1;
    // stop() in progress: the restore commands still go out, nothing else does.
    private volatile boolean closing;
    // Written on whichever thread negotiates (binder, or start()'s caller for an early
    // CONNECTED), read on the binder thread: volatile so the flag is never stale.
    private volatile boolean mtuRequested;
    // One more MTU request per link before a link whose MTU stayed too small for the 45-byte
    // state reports is recycled (a 23-byte MTU truncates every one of them).
    private boolean mtuRetried;
    // A connection attempt whose result reached the callback before connectGatt returned: the
    // stack settles the client registration on its binder thread first. start() retires that
    // handle (earlyLost) or finishes its CONNECTED handling (earlyConnected) instead of
    // publishing it as a live link that nothing drives. Guarded by this.
    private BluetoothGatt earlyLost, earlyConnected;
    // The current link reached CONNECTED; cleared when it is retired. Guarded by this.
    private boolean linkConnected;
    private boolean announced;
    // One warning per link when state reports arrive truncated (an MTU that stayed at 23).
    private int shortReportsLogged;
    /** Set after a with-response write was refused and the characteristic allows no-response. */
    private boolean forceNoResponse;
    private Pending inFlight;
    private boolean inFlightWithResponse;
    private volatile boolean stopped;

    /**
     * One queued GATT write: a feature command {id, length, payload...} for the command
     * characteristic, or a HID output report (id first, 0x80..0x89) for that report's own
     * characteristic. The report keeps its id so it can be re-framed if the firmware refuses it.
     */
    private static final class Pending {
        final BluetoothGattCharacteristic ch;
        final byte[] bytes;
        final boolean output;
        /** Part of applying a host settings write; must not be coalesced as a keep-alive. */
        final boolean hostSettings;

        Pending(BluetoothGattCharacteristic ch, byte[] bytes, boolean output) {
            this(ch, bytes, output, false);
        }

        Pending(BluetoothGattCharacteristic ch, byte[] bytes, boolean output, boolean hostSettings) {
            this.ch = ch;
            this.bytes = bytes;
            this.output = output;
            this.hostSettings = hostSettings;
        }

        int id() {
            return bytes[0] & 0xFF;
        }
    }
    private int reconnectAttempts;
    private boolean everConnected;

    // Armed on CONNECTED, disarmed by onReady/onLinkLost. A setup that stalls after an accepted
    // GATT operation used to keep `gatt` set forever, which also disarmed the manager's ACL
    // restart (it only restarts a driver whose link is down). Recycle the link instead.
    // Each watchdog is armed for one link (or one write, below) and carries that identity: a
    // runnable the handler has already dequeued when its target completes would otherwise run
    // against the successor and recycle a healthy link (A44-02). The fields hold the armed
    // instance so it can be disarmed; disarming keeps the reference (harmless to re-remove).
    private Runnable setupWatchdog, connectWatchdog;

    private Runnable setupWatchdogFor(final BluetoothGatt armed) {
        return () -> {
            BluetoothGatt g = gatt;
            if (g == armed && g != null && !announced && !stopped) {
                LimeLog.warning("Steam Controller BLE: setup did not complete within " + SETUP_WATCHDOG_MS + " ms; recycling the link");
                onLinkLost(g);
            }
        };
    }

    // Armed by start() around connectGatt, disarmed on CONNECTED and when the link is retired.
    private Runnable connectWatchdogFor(final BluetoothGatt armed) {
        return () -> {
            BluetoothGatt g = gatt;
            boolean pending;
            synchronized (this) {
                pending = g == armed && g != null && !linkConnected && !stopped;
            }
            if (pending) {
                LimeLog.warning("Steam Controller BLE: no connection result within " + CONNECT_WATCHDOG_MS + " ms; recycling the link");
                onLinkLost(g);
            }
        };
    }

    private void armSetupWatchdog(BluetoothGatt g) {
        disarmSetupWatchdog();
        setupWatchdog = setupWatchdogFor(g);
        handler.postDelayed(setupWatchdog, SETUP_WATCHDOG_MS);
    }

    private void disarmSetupWatchdog() {
        if (setupWatchdog != null) handler.removeCallbacks(setupWatchdog);
    }

    private void armConnectWatchdog(BluetoothGatt g) {
        disarmConnectWatchdog();
        connectWatchdog = connectWatchdogFor(g);
        handler.postDelayed(connectWatchdog, CONNECT_WATCHDOG_MS);
    }

    private void disarmConnectWatchdog() {
        if (connectWatchdog != null) handler.removeCallbacks(connectWatchdog);
    }
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
                              int rumbleHoldMs, int rumbleMethod, int hapticPath, int hapticGain, boolean stickRim) {
        super(deviceId, listener, PRODUCT_ID_IBEX_BLE, context, device.getAddress(), "BLE",
                motionEnabled, splitPads, gripsMode, stickRim);
        this.context = context.getApplicationContext();
        this.device = device;
        this.rumbleHoldMs = Math.max(0, rumbleHoldMs);
        this.rumbleMethod = rumbleMethod;
        this.hapticPath = hapticPath;
        this.hapticGain = hapticGain;
        // This transport applies Steam's settings writes; the wired one does not (yet).
        capabilities = (short) (capabilities | MoonBridge.LI_CCAP_STEAM_SETTINGS);
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
        BluetoothGatt lost = null, reached = null;
        boolean published = false;
        synchronized (this) {
            connecting = false;
            if (!stopped) {
                gatt = connected;
                published = true;
                if (connected != null) {
                    if (earlyLost == connected) {
                        lost = connected;
                    } else if (earlyConnected == connected) {
                        reached = connected;
                    } else {
                        armConnectWatchdog(connected);
                    }
                }
                earlyLost = null;
                earlyConnected = null;
            }
        }
        if (published) {
            if (lost != null) {
                // The stack refused the attempt (client registration failed) before connectGatt
                // returned: retire the handle the way the DISCONNECTED would have, instead of
                // leaving it published as a link that nothing will ever drive.
                LimeLog.warning("Steam Controller BLE: the connection attempt failed before connectGatt returned");
                onLinkLost(lost);
                return false;
            }
            if (reached != null) {
                onConnected(reached);
            }
            return connected != null;
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
        boolean restore, wasAnnounced;
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
                writeQueue.add(motorsOff());
                writeQueue.add(new Pending(writeChar, new byte[]{ID_SET_DEFAULT_DIGITAL_MAPPINGS, 0}, false));
                writeQueue.add(new Pending(writeChar, new byte[]{ID_LOAD_DEFAULT_SETTINGS, 0}, false));
            }
            // Read under the lock: onReady announces under it against `stopped`, so no
            // announcement can land after this point.
            wasAnnounced = announced;
            announced = false;
        }
        handler.removeCallbacksAndMessages(null);
        saveStickExtents();
        if (restore) {
            flushWrites();
            handler.postDelayed(closeLink, CLOSE_FALLBACK_MS);
        } else {
            closeLink.run();
        }
        if (wasAnnounced) {
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
            if (stopped) {
                return;
            }
            if (g != gatt) {
                // A result for an attempt connectGatt has not returned yet (the stack settles the
                // client registration on its binder thread first): keep it for start() to act on
                // once the handle is known, rather than dropping it. Anything else is a late
                // callback of a retired connection.
                synchronized (SteamControllerBle.this) {
                    if (connecting && gatt == null) {
                        if (newState == BluetoothProfile.STATE_CONNECTED && status == BluetoothGatt.GATT_SUCCESS) {
                            earlyConnected = g;
                        } else {
                            earlyLost = g;
                            // A late loss of a different handle says nothing about this attempt.
                            if (earlyConnected == g) earlyConnected = null;
                        }
                    }
                }
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
                onConnected(g);
            } else if (newState == BluetoothProfile.STATE_DISCONNECTED) {
                LimeLog.info("Steam Controller BLE: disconnected (status " + status + ")");
                onLinkLost(g);
            }
        }

        @SuppressLint("MissingPermission")
        @Override
        public void onMtuChanged(BluetoothGatt g, int mtu, int status) {
            final boolean retryMtu;
            synchronized (SteamControllerBle.this) {
                // Consume only this live link's outstanding request. Retirement
                // and a new connection publish their MTU state under this lock.
                if (stopped || g != gatt || !mtuRequested) {
                    return;
                }
                mtuRequested = false;
                retryMtu = mtu < STATE_REPORT_MTU && !mtuRetried;
                if (retryMtu) {
                    mtuRetried = true;
                    // Publish before GATT: the retry callback can arrive before
                    // requestMtu returns, including on another binder thread.
                    mtuRequested = true;
                }
            }
            LimeLog.info("Steam Controller BLE: MTU " + mtu);
            if (mtu < STATE_REPORT_MTU) {
                // A 23-byte MTU cannot carry a 45-byte state report. Ask once
                // more, then recycle instead of announcing a silent controller.
                if (retryMtu) {
                    LimeLog.warning("Steam Controller BLE: MTU " + mtu + " is too small for state reports; asking again");
                    if (!g.requestMtu(DESIRED_MTU)) {
                        // Retirement clears the flag only if g still owns it;
                        // this call may have returned after a new link started.
                        LimeLog.warning("Steam Controller BLE: MTU retry refused; recycling the known-small link");
                        onLinkLost(g);
                    }
                } else {
                    LimeLog.warning("Steam Controller BLE: MTU " + mtu + " stayed too small for state reports; recycling the link");
                    onLinkLost(g);
                }
                return;
            }
            if (!g.discoverServices()) onLinkLost(g);
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
                Arrays.fill(outputChars, null);
                resetOutputState();
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
                    } else if (writable && shortId >= WRITE_SHORT_LOW && shortId <= WRITE_SHORT_HIGH) {
                        outputChars[(int) (shortId - WRITE_SHORT_LOW)] = ch;
                        if (legacyWrite == null) {
                            legacyWrite = ch;
                        }
                    }
                }
                StringBuilder outputs = new StringBuilder();
                for (int i = 0; i < outputChars.length; i++) {
                    if (outputChars[i] != null) {
                        outputs.append(outputs.length() == 0 ? "" : ", ").append("0x")
                                .append(Integer.toHexString(OUTPUT_REPORT_FIRST + i))
                                .append(" (0x").append(Integer.toHexString(outputChars[i].getProperties())).append(')');
                    }
                }
                LimeLog.info("Steam Controller BLE: output report characteristics (properties): "
                        + (outputs.length() == 0 ? "none; haptics go as feature commands" : outputs));
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
            Pending failed;
            boolean withResponse;
            int framing = OUTPUT_FRAMING_PAYLOAD;
            int index = -1;
            synchronized (SteamControllerBle.this) {
                // Link loss can retire g after the check above while this callback
                // waits for the monitor. Only its own link may consume inFlight.
                if (g != gatt) {
                    return;
                }
                if (stopped && !closing) {
                    writeBusy = false;
                    return;
                }
                failed = inFlight;
                inFlight = null;
                writeRejectedSinceMs = -1;
                withResponse = inFlightWithResponse;
                if (failed != null && failed.output) {
                    index = failed.id() - OUTPUT_REPORT_FIRST;
                    framing = outputFraming[index];
                    if (withResponse && !outputProbed[index]) {
                        outputProbed[index] = true;
                        LimeLog.info("Steam Controller BLE: output report 0x" + Integer.toHexString(failed.id())
                                + (status == BluetoothGatt.GATT_SUCCESS ? " accepted" : " refused (status " + status + ")")
                                + " by the controller " + (framing == OUTPUT_FRAMING_PAYLOAD ? "without" : "with") + " its report id");
                    }
                    if (status == BluetoothGatt.GATT_SUCCESS) {
                        writtenReports[index]++;
                    }
                }
            }
            boolean retryWithoutResponse = false;
            int newFraming = framing;
            Pending retry = null;
            if (status != BluetoothGatt.GATT_SUCCESS) {
                LimeLog.warning("Steam Controller BLE: write failed, status " + status + describeCommand(failed)
                        + (withResponse ? " (with response)" : " (without response)"));
                if (failed != null && failed.output && status > 0 && status < 0x80) {
                    // An ATT error from the controller itself (not a stack/link code): the
                    // firmware refused the report as framed. Try it once with its report id;
                    // if that is refused too, this report id goes back to the feature-command
                    // conversion for the rest of the link (where one exists).
                    if (framing == OUTPUT_FRAMING_PAYLOAD) {
                        newFraming = OUTPUT_FRAMING_WITH_ID;
                        retry = failed;
                        LimeLog.warning("Steam Controller BLE: output report 0x" + Integer.toHexString(failed.id())
                                + " refused without its id; retrying with the report id");
                    } else if (framing == OUTPUT_FRAMING_WITH_ID) {
                        newFraming = OUTPUT_FRAMING_UNUSABLE;
                        retry = featureFallback(failed.bytes);
                        LimeLog.warning("Steam Controller BLE: output report 0x" + Integer.toHexString(failed.id())
                                + " refused with its id too; it goes as a feature command for this link"
                                + (retry == null ? " (none known: dropped)" : ""));
                    }
                } else if (withResponse && failed != null
                        && (ch.getProperties() & BluetoothGattCharacteristic.PROPERTY_WRITE_NO_RESPONSE) != 0) {
                    // Some stacks/firmware refuse a write request on a characteristic that only
                    // really serves write-without-response: switch this link over and re-send.
                    LimeLog.warning("Steam Controller BLE: switching to write-without-response for this link");
                    retryWithoutResponse = true;
                }
            }
            boolean drained;
            synchronized (SteamControllerBle.this) {
                // Failure logging and fallback selection run outside the lock.
                // A reconnect there must keep its write slot and watchdog.
                if (g != gatt) {
                    return;
                }
                disarmWriteWatchdog();
                writeBusy = false;
                if (retryWithoutResponse) {
                    forceNoResponse = true;
                    writeQueue.addFirst(failed);
                } else if (newFraming != framing) {
                    outputFraming[index] = newFraming;
                    // The first write in a new framing is a probe again: it goes with a
                    // response so the firmware's verdict reaches the log, and so a second
                    // refusal can be seen at all (a Write Command cannot be refused by the
                    // peer, so the feature fallback would otherwise never engage).
                    outputProbed[index] = false;
                    if (newFraming == OUTPUT_FRAMING_UNUSABLE) {
                        // Reports of this id queued for the output endpoint go the feature way too.
                        final int id = failed.id();
                        ArrayDeque<Pending> kept = new ArrayDeque<>();
                        for (Pending queued : writeQueue) {
                            Pending replacement = queued.output && queued.id() == id ? featureFallback(queued.bytes) : queued;
                            if (replacement != null) {
                                kept.add(replacement);
                            }
                        }
                        writeQueue.clear();
                        writeQueue.addAll(kept);
                    }
                    if (retry != null) {
                        writeQueue.addFirst(retry);
                    }
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
                // Report 0x44: the firmware's haptic acknowledgement / flow control
                // {actuator, flags, ...} (one per zero-repeat pulse on the wired unit).
                synchronized (SteamControllerBle.this) {
                    hapticAcks++;
                    lastAck = data;
                }
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
        disarmSetupWatchdog();
        LimeLog.info("Steam Controller BLE: haptic path: output reports "
                + (hapticPath == HAPTIC_PATH_OUTPUT_WITH_ID ? "with" : "without") + " their id; 0x82 gain: " + describeGain());
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
        handler.removeCallbacks(hapticStats);
        handler.postDelayed(hapticStats, HAPTIC_STATS_INTERVAL_MS);
        synchronized (this) {
            // Announce under the lock, against the stopped flag: a stop() landing between
            // the check on entry and here either sees `announced` and removes the context,
            // or has set `stopped` so no context is handed to a driver that is closing.
            // (A46-01; the listener's lock is always taken after this one.)
            if (stopped || announced) {
                return;
            }
            announced = true;
            LimeLog.info("Steam Controller BLE: ready, reporting as LI_CTYPE_STEAM");
            notifyDeviceAdded();
            // The listener now owns a fresh context. States received during setup,
            // or delivered to the previous connection, cannot satisfy this one.
            reportedStateValid = false;
        }
    }

    /** CONNECTED with success: negotiate the link (MTU, then services). */
    @SuppressLint("MissingPermission")
    private void onConnected(BluetoothGatt g) {
        synchronized (this) {
            // The unlocked g == gatt check on the way in can be stale: a watchdog may have
            // retired this handle meanwhile, and marking a retired link connected would leave
            // linkConnected true for the next link's connect watchdog.
            // stop() can also retain g while its restore writes drain, and a
            // duplicate CONNECTED must not reset the ongoing MTU retry budget.
            if (stopped || g != gatt || linkConnected) {
                return;
            }
            everConnected = true;
            linkConnected = true;
            mtuRetried = false;
            mtuRequested = true;
            disarmConnectWatchdog();
            armSetupWatchdog(g);
        }
        LimeLog.info("Steam Controller BLE: connected, negotiating");
        g.requestConnectionPriority(BluetoothGatt.CONNECTION_PRIORITY_HIGH);
        if (!g.requestMtu(DESIRED_MTU)) {
            synchronized (this) {
                // A rejected request can return after retirement and reconnect.
                // It must not consume the replacement's outstanding MTU request.
                if (stopped || g != gatt) {
                    return;
                }
                mtuRequested = false;
            }
            if (!g.discoverServices()) onLinkLost(g);
        }
    }

    @SuppressLint("MissingPermission")
    private void onLinkLost(BluetoothGatt g) {
        boolean wasAnnounced;
        synchronized (this) {
            // Retire the handle first, under the lock: a DISCONNECTED on the binder thread and a
            // watchdog on the handler thread can both arrive for one link, and only one of them
            // may clear its state and spend a reconnect attempt.
            if (g == null || g != gatt) {
                return;
            }
            gatt = null;
            linkConnected = false;
            mtuRequested = false;
            earlyLost = null;
            earlyConnected = null;
            disarmConnectWatchdog();
            handler.removeCallbacks(keepAlive);
            handler.removeCallbacks(hapticStats);
            handler.removeCallbacks(rumbleRefresh);
            handler.removeCallbacks(retryFlush);
            disarmWriteWatchdog();
            handler.removeCallbacks(batteryRead);
            disarmSetupWatchdog();
            writeQueue.clear();
            writeBusy = false;
            writeRejectedSinceMs = -1;
            writeChar = null;
            batteryChar = null;
            pendingSubscriptions.clear();
            stateSubscribed = false;
            stateChar = null;
            Arrays.fill(outputChars, null);
            resetOutputState();
            rumbleActive = false;
            rumbleReport = null;
            shortReportsLogged = 0;
            forceNoResponse = false;
            inFlight = null;
            wasAnnounced = announced;
            announced = false;
        }
        if (wasAnnounced) {
            // Release any touches the host still thinks are down
            releaseTouches();
            notifyDeviceRemoved();
        }
        try {
            g.close();
        } catch (SecurityException | IllegalStateException ignored) {
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

    private static String describeCommand(Pending p) {
        if (p == null || p.bytes.length == 0) {
            return "";
        }
        return (p.output ? " (output report 0x" : " (cmd 0x") + Integer.toHexString(p.id()) + ", "
                + p.bytes.length + (p.output ? " bytes with id)" : " bytes)");
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

    /**
     * The actuator a motors-off addresses, or -1 for an effect: a zero rumble (output 0x80 or
     * feature 0xEB), a zero-count or zero-duration pulse per side (0x81 / 0x8F), or an "off"
     * haptic command per side (0x82 / 0xEA). A stop is never evicted from the queue, and the
     * newest stop for an actuator supersedes the older ones still queued.
     */
    private static int stopKey(Pending p) {
        byte[] b = p.bytes;
        if (p.output) {
            switch (p.id()) {
                case 0x80:
                    return b.length >= 10 && b[4] == 0 && b[5] == 0 && b[7] == 0 && b[8] == 0 ? 0x8000 : -1;
                case 0x81:
                    // A zero on-duration is also silent, even when the repeat count is nonzero.
                    return b.length >= 8 && ((b[2] == 0 && b[3] == 0) || (b[6] == 0 && b[7] == 0))
                            ? (0x8100 | (b[1] & 0xFF)) : -1;
                case 0x82:
                    return b.length >= 4 && b[2] == 0 ? (0x8200 | (b[1] & 0xFF)) : -1;
                default:
                    return -1;
            }
        }
        if (b.length >= 9 && b[0] == ID_TRIGGER_RUMBLE_CMD) {
            return b[5] == 0 && b[6] == 0 && b[7] == 0 && b[8] == 0 ? 0xEB00 : -1;
        }
        if (b.length >= 9 && b[0] == ID_TRIGGER_HAPTIC_PULSE) {
            // Keep the same stop semantics as the driver, host and native callback queue.
            return (b[3] == 0 && b[4] == 0) || (b[7] == 0 && b[8] == 0) ? (0x8F00 | (b[2] & 0xFF)) : -1;
        }
        if (b.length >= 4 && b[0] == ID_TRIGGER_HAPTIC_CMD) {
            return b[3] == 0 ? (0xEA00 | (b[2] & 0xFF)) : -1;
        }
        return -1;
    }

    /** Queues a feature command on the command characteristic. */
    private void enqueueWrite(byte[] cmd) {
        BluetoothGattCharacteristic ch = writeChar;
        if (ch == null) {
            return;
        }
        enqueue(new Pending(ch, cmd, false));
    }

    /**
     * Queues a HID output report (id first) on that report's own characteristic. False when
     * this link offers none for it, or its output endpoints refused the framing (then the
     * caller falls back to the feature-command form, where one exists).
     */
    private boolean enqueueOutput(byte[] report) {
        Pending p = outputPending(report);
        if (p == null) {
            return false;
        }
        enqueue(p);
        return true;
    }

    private Pending outputPending(byte[] report) {
        BluetoothGattCharacteristic ch = outputChar(report[0] & 0xFF);
        return ch == null ? null : new Pending(ch, report, true);
    }

    /** The output characteristic for a report id on this link, if offered and not refused. */
    private synchronized BluetoothGattCharacteristic outputChar(int id) {
        int index = id - OUTPUT_REPORT_FIRST;
        if (index < 0 || index >= outputChars.length || outputFraming[index] == OUTPUT_FRAMING_UNUSABLE) {
            return null;
        }
        return outputChars[index];
    }

    /** Motors off: a zero 0x80 report when the firmware offers that endpoint, else the zero rumble command. */
    private Pending motorsOff() {
        Pending p = outputPending(plainRumbleReport(0, 0));
        return p != null ? p : new Pending(writeChar, rumbleCommand(0, 0), false);
    }

    /**
     * The feature-command form of a haptic output report, for a link whose output endpoints
     * refuse writes: 0x80 as ID_TRIGGER_RUMBLE_CMD, 0x81 as ID_TRIGGER_HAPTIC_PULSE, 0x82 as
     * ID_TRIGGER_HAPTIC_CMD. The LFO tone, log sweep and script reports have none known here.
     */
    private Pending featureFallback(byte[] r) {
        BluetoothGattCharacteristic ch = writeChar;
        if (ch == null) {
            return null;
        }
        switch (r[0] & 0xFF) {
            case 0x80:
                return r.length >= 10 ? new Pending(ch, rumbleCommandFromReport(r), false) : null;
            case 0x81:
                return r.length >= 8 ? new Pending(ch, firePulse(r[1], u16(r, 2), u16(r, 4), u16(r, 6)), false) : null;
            case 0x82:
                return r.length >= 4 ? new Pending(ch, triggerHaptic(r[1], r[2], r[3]), false) : null;
            default:
                return null;
        }
    }

    private void enqueue(Pending p) {
        synchronized (this) {
            if (closing || stopped || gatt == null || writeChar == null) {
                return;
            }
            int stop = stopKey(p);
            if (stop >= 0) {
                // The newest stop supersedes older stops for the same actuator, and
                // stays after any intervening effects. Never accumulate an unbounded
                // queue of "protected" stop commands while GATT is stalled.
                writeQueue.removeIf(queued -> stopKey(queued) == stop);
            } else if (p.output && p.id() == 0x80) {
                // Latest rumble state wins; an older queued 0x80, stop or not, is obsolete.
                writeQueue.removeIf(queued -> queued.output && queued.id() == 0x80);
            }
            if (!p.output && !p.hostSettings && p.bytes[0] == ID_SET_SETTINGS_VALUES) {
                // One keep-alive waiting is enough; a stalled link must not fill up with them.
                // Host writes and their post-reset setup are ordered commands. An earlier
                // keep-alive cannot replace them, so they neither count here nor get dropped.
                for (Pending queued : writeQueue) {
                    if (!queued.output && !queued.hostSettings && queued.bytes[0] == ID_SET_SETTINGS_VALUES) {
                        return;
                    }
                }
            }
            if (writeQueue.size() >= MAX_PENDING_WRITES) {
                // Drop the oldest command that is not a motors-off, so a stall cannot
                // leave a pad buzzing by evicting the stop and keeping the start.
                Pending victim = null;
                for (Pending queued : writeQueue) {
                    if (stopKey(queued) < 0) {
                        victim = queued;
                        break;
                    }
                }
                if (victim != null) {
                    writeQueue.remove(victim);
                    evictedWrites++;
                } else {
                    return;
                }
            }
            writeQueue.add(p);
        }
        flushWrites();
    }

    /** A timed-out operation cannot be distinguished from a later write's callback. */
    private Runnable writeWatchdog = writeWatchdogFor(0);   // never posted; replaced per write

    private Runnable writeWatchdogFor(final long attempt) {
        return () -> {
            BluetoothGatt expired;
            synchronized (this) {
                // Only the write this watchdog was armed for can expire it: a completion that
                // dequeued this runnable and dispatched the next write must not be charged.
                if (!writeBusy || writeAttempt != attempt) {
                    return;
                }
                expired = gatt;
            }
            LimeLog.warning("Steam Controller BLE: a write never completed; recycling the link");
            if (closing) closeLink.run();
            else if (expired != null) onLinkLost(expired);
        };
    }

    /** Called with the lock held, for the write attempt just handed to the stack. */
    private void armWriteWatchdog(long attempt) {
        disarmWriteWatchdog();
        writeWatchdog = writeWatchdogFor(attempt);
        handler.postDelayed(writeWatchdog, WRITE_WATCHDOG_MS);
    }

    private void disarmWriteWatchdog() {
        if (writeWatchdog != null) handler.removeCallbacks(writeWatchdog);
    }

    @SuppressLint("MissingPermission")
    private void flushWrites() {
        BluetoothGatt g;
        Pending p;
        byte[] value;
        boolean probe;
        boolean noResponse;
        long attempt;
        synchronized (this) {
            g = gatt;
            if (writeBusy || g == null || writeChar == null || (stopped && !closing)) {
                return;
            }
            p = writeQueue.poll();
            if (p == null) {
                return;
            }
            writeBusy = true;
            attempt = ++writeAttempt;
            // An output report goes without its id, as the notifications arrive, unless the
            // firmware refused that framing on this link. Its first write on a link goes with
            // a response, so the firmware's verdict on the report reaches the log.
            if (p.output) {
                int index = p.id() - OUTPUT_REPORT_FIRST;
                value = outputFraming[index] == OUTPUT_FRAMING_PAYLOAD
                        ? Arrays.copyOfRange(p.bytes, 1, p.bytes.length) : p.bytes;
                probe = !outputProbed[index]
                        && (p.ch.getProperties() & BluetoothGattCharacteristic.PROPERTY_WRITE) != 0;
            } else {
                value = p.bytes;
                probe = false;
            }
            // Haptics are fire-and-forget and frequent (40 ms rumble re-sends, Steam's click
            // pairs): without a response each costs no round trip on the link. The stack still
            // reports the write complete, so the one-at-a-time pacing holds either way.
            boolean haptic = p.output || p.bytes[0] == ID_TRIGGER_RUMBLE_CMD || p.bytes[0] == ID_TRIGGER_HAPTIC_CMD
                    || p.bytes[0] == ID_TRIGGER_HAPTIC_PULSE;
            noResponse = (haptic || forceNoResponse) && !probe
                    && (p.ch.getProperties() & BluetoothGattCharacteristic.PROPERTY_WRITE_NO_RESPONSE) != 0;
            // Binder can complete the write before writeCharacteristic returns.
            // Publish the report and its mode first so the callback owns both.
            inFlight = p;
            inFlightWithResponse = !noResponse;
        }
        BluetoothGattCharacteristic ch = p.ch;
        boolean accepted = false;
        try {
            ch.setWriteType(noResponse ? BluetoothGattCharacteristic.WRITE_TYPE_NO_RESPONSE
                    : BluetoothGattCharacteristic.WRITE_TYPE_DEFAULT);
            ch.setValue(value);
            accepted = g.writeCharacteristic(ch);
        } catch (SecurityException | IllegalStateException ignored) {
        }
        if (accepted) {
            synchronized (this) {
                // The completion may already have drained this write and dispatched
                // the next one. Do not overwrite or re-arm that newer operation.
                if (g != gatt || writeChar == null || writeAttempt != attempt || inFlight != p) return;
                writeRejectedSinceMs = -1;
                armWriteWatchdog(attempt);
            }
            return;
        }
        // Refused: another GATT operation is outstanding (a read, a descriptor write) or
        // the link is going down. Put the command back at the head and try again shortly.
        boolean expired;
        synchronized (this) {
            // A failed write may race link loss. Never put its command into a
            // replacement connection's queue or clear that connection's busy flag.
            if (g != gatt || writeChar == null || writeAttempt != attempt || inFlight != p || (stopped && !closing)) return;
            inFlight = null;
            writeBusy = false;
            long now = SystemClock.uptimeMillis();
            if (writeRejectedSinceMs < 0) writeRejectedSinceMs = now;
            expired = now - writeRejectedSinceMs >= WRITE_WATCHDOG_MS;
            writeQueue.addFirst(p);
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
                sendRumble(rumbleReport);
                handler.postDelayed(this, Math.min(RUMBLE_CMD_RESEND_MS, remaining));
            } else if (rumbleMethod == RUMBLE_METHOD_PULSES) {
                // Bound the train to the hold window so the last one ends on time by itself.
                int trainMs = (int) Math.min(RUMBLE_TRAIN_MS, remaining);
                int repeat = Math.max(1, trainMs * 1000 / RUMBLE_PERIOD_US);
                sendPulseTrain(SIDE_LEFT, rumbleLow, repeat);
                sendPulseTrain(SIDE_RIGHT, rumbleHigh, repeat);
                handler.postDelayed(this, Math.min(RUMBLE_REFRESH_MS, remaining));
            } else {
                lastRumbleWriteMs = now;
                sendRumble(plainRumbleReport(rumbleLowRaw, rumbleHighRaw));
                handler.postDelayed(this, Math.min(RUMBLE_CMD_RESEND_MS, remaining));
            }
        }
    };

    /** Motors off, whichever way they were driven (a zero rumble is also a stop). */
    private void stopRumble() {
        if (rumbleMethod == RUMBLE_METHOD_PULSES && rumbleReport == null) {
            sendPulse(SIDE_LEFT, 0, 0, 0);
            sendPulse(SIDE_RIGHT, 0, 0, 0);
        } else {
            sendRumble(plainRumbleReport(0, 0));
        }
        rumbleReport = null;
    }

    /** A 0x80 rumble report on its output characteristic, or the same values as ID_TRIGGER_RUMBLE_CMD. */
    private void sendRumble(byte[] report) {
        if (!enqueueOutput(report)) {
            enqueueWrite(rumbleCommandFromReport(report));
        }
    }

    /** A 0x81 pulse on its output characteristic, or as ID_TRIGGER_HAPTIC_PULSE (repeat 0 = stop that side). */
    private void sendPulse(byte side, int onUs, int offUs, int repeat) {
        byte[] report = {REPORT_PULSE, side,
                (byte) onUs, (byte) (onUs >> 8),
                (byte) offUs, (byte) (offUs >> 8),
                (byte) repeat, (byte) (repeat >> 8)};
        if (!enqueueOutput(report)) {
            enqueueWrite(firePulse(side, onUs, offUs, repeat));
        }
    }

    /** A pulse train of the given duty cycle on one pad (repeat 0 = stop the running train). */
    private void sendPulseTrain(byte side, float magnitude, int repeat) {
        int onUs = 0, offUs = 0;
        if (magnitude > 0.01f && repeat > 0) {
            float duty = 0.25f + 0.72f * Math.min(1f, magnitude);
            onUs = (int) (RUMBLE_PERIOD_US * duty);
            offUs = RUMBLE_PERIOD_US - onUs;
        } else {
            repeat = 0;
        }
        sendPulse(side, onUs, offUs, repeat);
    }

    /**
     * A host's Steam settings write for the controller: over USB Steam configures the unit at
     * connect and when its controller UI opens (pad modes, LED, sleep timeout, and the haptic
     * gains and intensities that decide how a pulse feels), and the virtual controller on the
     * host forwards those writes here. They go to the command characteristic with their report
     * id, exactly as this link's own setup does, minus {@link #HOST_SETTINGS_RESERVED}. A
     * LOAD_DEFAULT_SETTINGS (Steam's exit) is applied and followed by this link's own setup, so
     * lizard mode and the watchdog do not come back for up to a keep-alive interval.
     */
    @Override
    public void steamSettings(byte command, byte[] triples) {
        final byte[] copy = triples == null ? new byte[0] : triples.clone();
        handler.post(() -> {
            if (stopped || gatt == null || writeChar == null) {
                return;
            }
            if (command == ID_LOAD_DEFAULT_SETTINGS) {
                LimeLog.info("Steam Controller BLE: host settings 0x8e (load defaults), re-sending this link's setup");
                enqueue(new Pending(writeChar, new byte[]{ID_LOAD_DEFAULT_SETTINGS, 0}, false, true));
                // A keep-alive already queued before this reset is undone by it.
                // Keep this restoration after its reset, including repeated resets.
                enqueue(new Pending(writeChar, settings(SETTING_LIZARD_MODE, 0, SETTING_STEAM_WATCHDOG_ENABLE, 0,
                        SETTING_IMU_MODE, motionEnabled ? (IMU_MODE_RAW_ACCEL | IMU_MODE_RAW_GYRO) : 0), false, true));
                return;
            }
            if (command != ID_SET_SETTINGS_VALUES) {
                return;
            }
            int triplesIn = copy.length / 3;
            byte[] cmd = new byte[2 + 3 * triplesIn];
            StringBuilder applied = new StringBuilder(), reserved = new StringBuilder();
            int kept = 0;
            for (int i = 0; i < triplesIn; i++) {
                int id = copy[3 * i] & 0xFF;
                int value = (copy[3 * i + 1] & 0xFF) | ((copy[3 * i + 2] & 0xFF) << 8);
                if (isHostSettingReserved(id)) {
                    reserved.append(reserved.length() == 0 ? "" : " ").append(id);
                    continue;
                }
                cmd[2 + 3 * kept] = (byte) id;
                cmd[3 + 3 * kept] = copy[3 * i + 1];
                cmd[4 + 3 * kept] = copy[3 * i + 2];
                kept++;
                applied.append(applied.length() == 0 ? "" : " ").append(id).append('=').append(value);
            }
            LimeLog.info("Steam Controller BLE: host settings 0x87: " + (kept == 0 ? "nothing to apply" : applied)
                    + (reserved.length() == 0 ? "" : " (reserved, kept ours: " + reserved + ")"));
            if (kept == 0) {
                return;
            }
            cmd[0] = ID_SET_SETTINGS_VALUES;
            cmd[1] = (byte) (3 * kept);
            enqueue(new Pending(writeChar, kept == triplesIn ? cmd : Arrays.copyOf(cmd, 2 + 3 * kept), false, true));
        });
    }

    private static boolean isHostSettingReserved(int id) {
        for (int reserved : HOST_SETTINGS_RESERVED) {
            if (reserved == id) {
                return true;
            }
        }
        return false;
    }

    /**
     * A host's Steam Controller haptic output report (0x80 rumble, 0x81 pulse, 0x82 command,
     * 0x83 LFO tone, 0x84 log sweep, 0x85 script), written as-is to that report's own
     * characteristic, the way the USB transport writes it to the wired unit: Steam's trackpad
     * clicks are 0x81 pulse pairs and 0x82 commands. A 0x80 is re-sent until the host's zero or
     * the hold limit. Links without the output endpoints replay 0x80..0x82 as the firmware's
     * feature messages instead.
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
            int id = copy[0] & 0xFF;
            int size = outputSize(id);
            // Invalid side values must not consume the finite stop-priority queue.
            boolean usable = size != 0 && copy.length >= size
                    && !((id == 0x81 || id == 0x82) && (copy[1] & 0xFF) > 2);
            noteHostReport(copy, usable);
            if (!usable) {
                return;
            }
            byte[] r = copy.length == size ? copy : Arrays.copyOf(copy, size);
            if (id == 0x82 && hapticGain != HAPTIC_GAIN_HOST) {
                // Diagnostic: the gain byte of a tick or click command, which the wired unit
                // gets as -3 / -14 dB and the streamed one has only ever got as 0.
                int cmd = r[2] & 0xFF;
                if (cmd == HAPTIC_CMD_TICK || cmd == HAPTIC_CMD_CLICK) {
                    byte gain = hapticGain == HAPTIC_GAIN_PLUS6 ? PLUS6_GAIN_DB
                            : cmd == HAPTIC_CMD_CLICK ? USB_CLICK_GAIN_DB : USB_TICK_GAIN_DB;
                    if (r[3] != gain) {
                        r[3] = gain;
                        synchronized (this) {
                            hostGainOverrides++;
                        }
                    }
                }
            }
            if (id != 0x80) {
                if (!enqueueOutput(r)) {
                    Pending fallback = featureFallback(r);
                    if (fallback != null) {
                        enqueue(fallback);
                    }
                }
                return;
            }
            handler.removeCallbacks(rumbleRefresh);
            boolean silent = u16(r, 4) == 0 && u16(r, 7) == 0;
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
            rumbleReport = r;
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
        });
    }

    /** New link: every output report starts in the preferred framing and unprobed, and the counters restart. */
    private synchronized void resetOutputState() {
        Arrays.fill(outputFraming, hapticPath == HAPTIC_PATH_OUTPUT_WITH_ID ? OUTPUT_FRAMING_WITH_ID : OUTPUT_FRAMING_PAYLOAD);
        Arrays.fill(outputProbed, false);
        Arrays.fill(hostReports, 0);
        Arrays.fill(writtenReports, 0);
        hostStops = hostIgnored = evictedWrites = hapticAcks = 0;
        lastAck = null;
    }

    /** Counts a host haptic report for the summary, and logs the first few one by one. */
    private void noteHostReport(byte[] report, boolean usable) {
        int id = report[0] & 0xFF;
        int index = id - OUTPUT_REPORT_FIRST;
        long now = SystemClock.uptimeMillis();
        int sample = 0;
        long sinceFirst;
        synchronized (this) {
            if (hostReportsSeen++ == 0) {
                firstHostReportMs = now;
            }
            sinceFirst = now - firstHostReportMs;
            boolean known = index >= 0 && index < hostSamplesById.length;
            if ((known ? hostSamplesById[index] : hostSamplesOther) < HOST_SAMPLES_PER_ID) {
                if (known) {
                    hostSamplesById[index]++;
                } else {
                    hostSamplesOther++;
                }
                sample = ++hostSamplesLogged;
            }
            if (!usable) {
                hostIgnored++;
            } else {
                if (index >= 0 && index < hostReports.length) {
                    hostReports[index]++;
                }
                if (stopKey(new Pending(null, report, true)) >= 0) {
                    hostStops++;
                }
            }
        }
        if (sample != 0) {
            LimeLog.info("Steam Controller BLE: host haptic report #" + sample + " at +" + sinceFirst
                    + " ms: " + hex(report) + (usable ? "" : " (ignored)"));
        }
    }

    /** Every 10 s while the link is up: what the host sent and what reached the controller. */
    private final Runnable hapticStats = new Runnable() {
        @Override
        public void run() {
            if (stopped || gatt == null) {
                return;
            }
            StringBuilder host = new StringBuilder(), written = new StringBuilder();
            int stops, ignored, evicted, acks, overrides;
            byte[] ack;
            synchronized (SteamControllerBle.this) {
                for (int i = 0; i < hostReports.length; i++) {
                    if (hostReports[i] != 0) {
                        host.append(host.length() == 0 ? "" : ", ").append("0x")
                                .append(Integer.toHexString(OUTPUT_REPORT_FIRST + i)).append(" x").append(hostReports[i]);
                    }
                    if (writtenReports[i] != 0) {
                        written.append(written.length() == 0 ? "" : ", ").append("0x")
                                .append(Integer.toHexString(OUTPUT_REPORT_FIRST + i)).append(" x").append(writtenReports[i]);
                    }
                }
                stops = hostStops;
                ignored = hostIgnored;
                evicted = evictedWrites;
                acks = hapticAcks;
                overrides = hostGainOverrides;
                ack = lastAck;
                Arrays.fill(hostReports, 0);
                Arrays.fill(writtenReports, 0);
                hostStops = hostIgnored = evictedWrites = hapticAcks = hostGainOverrides = 0;
            }
            if (host.length() != 0 || written.length() != 0 || ignored != 0 || acks != 0) {
                LimeLog.info("Steam Controller BLE: haptics, last " + (HAPTIC_STATS_INTERVAL_MS / 1000) + " s: host "
                        + (host.length() == 0 ? "none" : host) + " (" + stops + " stops, " + ignored + " ignored); written "
                        + (written.length() == 0 ? "none" : written) + "; evicted " + evicted + "; acks " + acks
                        + (ack != null ? " (last " + hex(ack) + ")" : "")
                        + (overrides != 0 ? "; 0x82 gain overridden x" + overrides : ""));
            }
            handler.postDelayed(this, HAPTIC_STATS_INTERVAL_MS);
        }
    };

    private String describeGain() {
        switch (hapticGain) {
            case HAPTIC_GAIN_USB: return "the wired unit's values (tick -3 dB, click -14 dB)";
            case HAPTIC_GAIN_PLUS6: return "+6 dB";
            default: return "as the host sends it";
        }
    }

    /** Wire size, id included, of the haptic output reports the host forwards (sc26_usb.h); 0 for others. */
    private static int outputSize(int id) {
        switch (id) {
            case 0x80: return 10;
            case 0x81: return 8;
            case 0x82: return 4;
            case 0x83: return 10;
            case 0x84: return 9;
            case 0x85: return 4;
            default: return 0;
        }
    }

    /** A 0x80 rumble report {type, intensity, left{speed, gain}, right{speed, gain}} from plain motor values. */
    private static byte[] plainRumbleReport(int leftSpeed, int rightSpeed) {
        return new byte[]{
                REPORT_RUMBLE,
                0,                                  // type
                0, 0,                               // intensity
                (byte) leftSpeed, (byte) (leftSpeed >> 8), 0,
                (byte) rightSpeed, (byte) (rightSpeed >> 8), 0};
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
