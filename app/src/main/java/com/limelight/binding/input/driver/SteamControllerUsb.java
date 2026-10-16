package com.limelight.binding.input.driver;

import android.content.Context;
import android.hardware.usb.UsbConstants;
import android.hardware.usb.UsbDevice;
import android.hardware.usb.UsbDeviceConnection;
import android.hardware.usb.UsbEndpoint;
import android.hardware.usb.UsbInterface;
import android.os.Handler;
import android.os.HandlerThread;
import android.os.Process;
import android.os.SystemClock;

import com.limelight.LimeLog;
import com.limelight.nvstream.input.SteamControllerAttributes;
import com.limelight.nvstream.jni.MoonBridge;

import java.util.ArrayDeque;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.Iterator;
import java.util.List;

/**
 * SC2026 USB transport. Proteus exposes four independent controller interfaces (2..5);
 * interfaces 0/1 belong to the receiver and must not be claimed or configured as gamepads.
 * Each slot shares the BLE input mapper, but uses native HID feature/output reports.
 * Protocol reference: SDL's SDL_hidapi_steam_triton.c and steam/controller_structs.h.
 */
public final class SteamControllerUsb {
    private static final int VALVE = 0x28DE, WIRED = 0x1302, PUCK = 0x1304;
    // Android reports a timeout, a STALL and a detach alike (-1); a slow SET_REPORT on a busy
    // receiver must not flap the controller, so a slot is retired only after several
    // consecutive failures (input staleness remains the authority for a gone controller).
    private static final int IO_TIMEOUT_MS = 250;
    private static final int OUTPUT_FAILURES_BEFORE_DISCONNECT = 3;
    private static final int READ_TIMEOUT_MS = 250;
    private static final int KEEPALIVE_MS = 1000;
    private static final int STALE_STATE_MS = 3000;
    private static final int MAX_OUTPUTS = 32;
    private static final int OUTPUT_RETRY_MS = 40;
    // Firmware settings written through feature report 1 (ids as in SteamControllerBle).
    private static final byte SETTING_LIZARD_MODE = 9, SETTING_IMU_MODE = 48, SETTING_STEAM_WATCHDOG_ENABLE = 71;
    private static final byte IMU_MODE_RAW = 0x18;   // RAW_ACCEL | RAW_GYRO
    // Feature-report queries answered through GET_REPORT: the real unit's attributes for the host.
    private static final byte ID_GET_ATTRIBUTES_VALUES = (byte) 0x83, ID_GET_STRING_ATTRIBUTE = (byte) 0xAE,
            ID_GET_DEVICE_INFO = (byte) 0xF2;
    // Steam's length byte on GET_STRING_ATTRIBUTE (the firmware reads only the tag).
    private static final int STRING_ATTRIBUTE_REQUEST_LENGTH = 0x15;

    private final UsbDevice device;
    private final UsbDeviceConnection connection;
    private final List<Slot> slots = new ArrayList<>();
    private final HandlerThread outputThread = new HandlerThread("SteamControllerUsb", Process.THREAD_PRIORITY_URGENT_DISPLAY);
    private Handler outputHandler;
    private volatile boolean stopped;
    private boolean started;

    public static boolean canClaimDevice(UsbDevice device) {
        return device != null && device.getVendorId() == VALVE &&
                (device.getProductId() == PUCK || device.getProductId() == WIRED);
    }

    public SteamControllerUsb(UsbDevice device, UsbDeviceConnection connection, int firstId,
                              UsbDriverListener listener, Context context, boolean motionEnabled,
                              boolean splitPads, int gripsMode, int rumbleHoldMs, boolean stickRim) {
        this.device = device;
        this.connection = connection;
        boolean[] seen = new boolean[6];
        for (int n = 0; n < device.getInterfaceCount(); n++) {
            UsbInterface iface = device.getInterface(n);
            int id = iface.getId();
            if (iface.getInterfaceClass() != UsbConstants.USB_CLASS_HID || iface.getAlternateSetting() != 0 ||
                    (device.getProductId() == PUCK ? id < 2 || id > 5 : id != 0)) {
                continue;
            }
            if (seen[id]) continue;
            seen[id] = true;
            UsbEndpoint in = null, out = null;
            boolean duplicate = false;
            for (int e = 0; e < iface.getEndpointCount(); e++) {
                UsbEndpoint endpoint = iface.getEndpoint(e);
                if (endpoint.getType() != UsbConstants.USB_ENDPOINT_XFER_INT) continue;
                if (endpoint.getDirection() == UsbConstants.USB_DIR_IN) {
                    duplicate |= in != null;
                    in = endpoint;
                } else {
                    duplicate |= out != null;
                    out = endpoint;
                }
            }
            // Wired 0x42 state reports are 54 bytes; the puck's 0x45/0x47 frames are 46.
            final int minimumPacket = device.getProductId() == PUCK ? 46 : 54;
            if (!duplicate && in != null && in.getMaxPacketSize() >= minimumPacket) {
                // Use the interface ID, not descriptor order, as the puck slot identity.
                int slotId = device.getProductId() == PUCK ? id - 2 : 0;
                slots.add(new Slot(firstId + slotId, listener, context, iface, in, out,
                        motionEnabled, splitPads, gripsMode, rumbleHoldMs, stickRim));
            }
        }
    }

    public int getUsbDeviceId() {
        return device.getDeviceId();
    }

    /** Takes ownership of the connection, including cleanup if an interface cannot be claimed. */
    public boolean start() {
        if (started || stopped) return false;
        try {
            if (!canClaimDevice(device) || slots.isEmpty()) {
                LimeLog.warning("Steam Controller USB: no usable interface on " + device.getDeviceName()
                        + " (product " + Integer.toHexString(device.getProductId()) + "); not claiming it");
                stopped = true;
                releaseResources();
                return false;
            }
            for (Slot slot : slots) {
                if (!connection.claimInterface(slot.iface, true)) {
                    stopped = true;
                    releaseResources();
                    return false;
                }
                slot.claimed = true;
            }
            outputThread.start();
            outputHandler = new Handler(outputThread.getLooper());
            started = true;
            for (Slot slot : slots) {
                slot.reader = new Thread(slot::readLoop, "SteamControllerUsb-" + slot.iface.getId());
                slot.reader.start();
            }
            outputHandler.post(keepAlive);
            return true;
        } catch (RuntimeException e) {
            LimeLog.warning("Steam Controller USB: startup failed: " + e);
            if (started) stop();
            else {
                stopped = true;
                releaseResources();
                outputThread.quitSafely();
            }
            return false;
        }
    }

    private void releaseResources() {
        try {
            for (Slot slot : slots) {
                if (slot.claimed) {
                    try { connection.releaseInterface(slot.iface); } catch (RuntimeException ignored) { }
                    slot.claimed = false;
                }
            }
        } finally {
            connection.close();
        }
    }

    public void reportAttachedControllers(UsbDriverListener listener) {
        for (Slot slot : slots) {
            synchronized (slot) {
                if (!stopped && slot.connected) {
                    slot.resetInputState();
                    listener.deviceAdded(slot);
                }
            }
        }
    }

    /** Retire input immediately. Bounded restore writes and USB release run off the UI thread. */
    public void stop() {
        synchronized (this) {
            if (stopped) return;
            stopped = true;
        }
        if (!started) return;
        outputHandler.removeCallbacksAndMessages(null);
        List<Slot> restore = new ArrayList<>();
        for (Slot slot : slots) {
            synchronized (slot) {
                if (slot.connected) restore.add(slot);
                slot.disconnect();
            }
            if (slot.reader != null) slot.reader.interrupt();
        }
        outputHandler.post(() -> {
            try {
                for (Slot slot : restore) {
                    // Stop internal rumble and both pad pulse/command engines.
                    writeOutput(slot, new byte[]{(byte) 0x80, 0, 0, 0, 0, 0, 0, 0, 0, 0});
                    for (byte side = 0; side < 2; side++) {
                        writeOutput(slot, new byte[]{(byte) 0x81, side, 0, 0, 0, 0, 0, 0});
                        writeOutput(slot, new byte[]{(byte) 0x82, side, 0, 0});
                    }
                    configure(slot, false);
                }
            } finally {
                releaseResources();
                outputThread.quitSafely();
            }
        });
    }

    private final Runnable keepAlive = new Runnable() {
        @Override public void run() {
            if (stopped) return;
            for (Slot slot : slots) {
                int generation;
                synchronized (slot) {
                    if (!slot.connected) continue;
                    generation = slot.generation;
                }
                boolean ok = configure(slot, true);
                synchronized (slot) {
                    if (slot.connected && slot.generation == generation && slot.configureState != (ok ? 1 : 2)) logLinkSettings(slot, ok);
                }
                if (ok) {
                    slot.writeSucceeded();
                    // Initial setup can fail without retiring the slot. Read its attributes
                    // after the first successful retry, once per connected generation.
                    readAttributes(slot, generation);
                } else slot.writeFailed(generation);
            }
            if (!stopped) outputHandler.postDelayed(this, KEEPALIVE_MS);
        }
    };

    private boolean configure(Slot slot, boolean active) {
        // HID feature report 1: SET_SETTINGS_VALUES(0x87), three (id,u16) triples, framed as
        // Steam frames it on the wire (SET_REPORT, wValue 0x0301, 64 bytes starting 01 87).
        // Lizard mode off and the raw IMU on while active; lizard mode back on and the IMU
        // off at stop. The firmware's Steam watchdog (71) stays disabled, which is the unit's
        // default and what Steam's own USB session and the BLE link leave it at. Up to
        // fork.118 this transport armed it ("if Android dies, lizard mode can recover"): a
        // watchdog that waits for Steam's heartbeat and never sees it reverts the settings,
        // which turns the IMU off until the next keep-alive re-arms it, and the
        // cable-connected unit was the one path whose gyro did not follow Steam's glyph
        // (fork.119; the puck's receiver keeps its own radio traffic, BLE never armed it).
        byte[] feature = new byte[64];
        feature[0] = 1; feature[1] = (byte) 0x87; feature[2] = 9;
        feature[3] = SETTING_LIZARD_MODE; feature[4] = (byte) (active ? 0 : 1);
        feature[6] = SETTING_IMU_MODE; feature[7] = (byte) (active && slot.motionEnabled ? IMU_MODE_RAW : 0);
        feature[9] = SETTING_STEAM_WATCHDOG_ENABLE; feature[10] = 0;
        try {
            return connection.controlTransfer(0x21, 0x09, 0x0301, slot.iface.getId(),
                    feature, feature.length, IO_TIMEOUT_MS) == feature.length;
        } catch (RuntimeException e) {
            return false;
        }
    }

    /** Once per connect and whenever the write's result changes: what this transport asked of the firmware. */
    private void logLinkSettings(Slot slot, boolean ok) {
        slot.configureState = (byte) (ok ? 1 : 2);
        LimeLog.info("Steam Controller USB slot " + slot.iface.getId() + ": link settings (feature report 1, interface "
                + slot.iface.getId() + "): lizard mode 0, IMU mode 0x" + Integer.toHexString(slot.motionEnabled ? IMU_MODE_RAW : 0)
                + ", Steam watchdog 0 -> " + (ok ? "accepted" : "write failed"));
    }

    /**
     * Once per connect, after the settings write: the real unit's firmware attributes for the host
     * (GET_ATTRIBUTES_VALUES, GET_DEVICE_INFO sub 0, the unit and board serials), each asked the way
     * Steam asks: SET_REPORT of the command on feature report 1 (wValue 0x0301, wIndex = interface,
     * 64 bytes starting 01 <type> <len>), then GET_REPORT of the same report for the reply
     * (01 <type> <len> payload). Over the puck the exchange reaches the paired controller. A failed
     * query leaves its fields out of the valid mask and does not count against the output budget.
     */
    private void readAttributes(Slot slot, int generation) {
        synchronized (slot) {
            if (stopped || !slot.connected || slot.generation != generation || slot.attributesQueried) return;
            slot.attributesQueried = true;
        }
        SteamControllerAttributes attributes = new SteamControllerAttributes(device.getProductId() == PUCK
                ? MoonBridge.LI_SCATTR_TRANSPORT_PUCK : MoonBridge.LI_SCATTR_TRANSPORT_USB);
        byte[] reply = featureQuery(slot, ID_GET_ATTRIBUTES_VALUES, 0);
        if (reply != null) attributes.parseAttributeValues(reply, 0, reply.length);
        reply = featureQuery(slot, ID_GET_DEVICE_INFO, 1, (byte) 0);
        if (reply != null) attributes.parseDeviceInfo(reply, 0, reply.length);
        reply = featureQuery(slot, ID_GET_STRING_ATTRIBUTE, STRING_ATTRIBUTE_REQUEST_LENGTH, (byte) 1);
        if (reply != null) attributes.parseStringAttribute(reply, 0, reply.length);
        reply = featureQuery(slot, ID_GET_STRING_ATTRIBUTE, STRING_ATTRIBUTE_REQUEST_LENGTH, (byte) 0);
        if (reply != null) attributes.parseStringAttribute(reply, 0, reply.length);
        synchronized (slot) {
            if (stopped || !slot.connected || slot.generation != generation) return;
            slot.publishSteamAttributes(attributes);
        }
    }

    /** The payload of one feature-report query's reply (after 01 <type> <len>), or null when it failed. */
    private byte[] featureQuery(Slot slot, byte type, int length, byte... payload) {
        byte[] command = new byte[64];
        command[0] = 1; command[1] = type; command[2] = (byte) length;
        System.arraycopy(payload, 0, command, 3, payload.length);
        byte[] reply = new byte[64];
        int read;
        try {
            if (connection.controlTransfer(0x21, 0x09, 0x0301, slot.iface.getId(), command, command.length, IO_TIMEOUT_MS) != command.length) {
                LimeLog.warning("Steam Controller USB slot " + slot.iface.getId() + ": attribute query 0x"
                        + Integer.toHexString(type & 0xFF) + " not accepted");
                return null;
            }
            read = connection.controlTransfer(0xA1, 0x01, 0x0301, slot.iface.getId(), reply, reply.length, IO_TIMEOUT_MS);
        } catch (RuntimeException e) {
            read = -1;
        }
        if (read < 3 || reply[0] != 1 || reply[1] != type) {
            LimeLog.warning("Steam Controller USB slot " + slot.iface.getId() + ": no reply to attribute query 0x"
                    + Integer.toHexString(type & 0xFF) + (read < 0 ? "" : " (" + read + " bytes)"));
            return null;
        }
        return Arrays.copyOfRange(reply, 3, 3 + Math.min(reply[2] & 0xFF, read - 3));
    }

    private boolean writeOutput(Slot slot, byte[] report) {
        try {
            if (slot.out != null) {
                return connection.bulkTransfer(slot.out, report, report.length, IO_TIMEOUT_MS) == report.length;
            }
            // HID permits output reports on EP0 when no interrupt OUT endpoint exists.
            return connection.controlTransfer(0x21, 0x09, 0x0200 | (report[0] & 0xff),
                    slot.iface.getId(), report, report.length, IO_TIMEOUT_MS) == report.length;
        } catch (RuntimeException e) {
            return false;
        }
    }

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

    private static int stopKey(byte[] report) {
        int id = report[0] & 0xff;
        if (id == 0x80 && report[4] == 0 && report[5] == 0 && report[7] == 0 && report[8] == 0) return 0x80;
        if ((id == 0x81 && ((report[6] == 0 && report[7] == 0) || (report[2] == 0 && report[3] == 0))) ||
                (id == 0x82 && report[2] == 0)) {
            return (id << 8) | (report[1] & 0xff);
        }
        return -1;
    }

    private final class Slot extends SteamControllerInput {
        final UsbInterface iface;
        final UsbEndpoint in, out;
        final int rumbleHoldMs;
        boolean claimed;
        Thread reader;
        boolean connected;
        int generation;
        long lastStateMs, retryAtMs;
        final ArrayDeque<byte[]> outputs = new ArrayDeque<>();
        boolean drainScheduled;
        boolean attributesQueried;   // one query sequence after successful setup for this generation
        byte configureState;   // settings write for this generation: 0 not logged yet, 1 accepted, 2 failed
        byte[] repeatingRumble;
        long rumbleDeadline;

        Slot(int id, UsbDriverListener listener, Context context, UsbInterface iface, UsbEndpoint in,
             UsbEndpoint out, boolean motion, boolean split, int grips, int holdMs, boolean rim) {
            // A puck slot may host a different controller after reconnect. Do not persist its
            // learned stick extents under the receiver's identity or share BLE calibration.
            super(id, listener, device.getProductId(), context, null, "USB slot " + iface.getId(),
                    motion, split, grips, rim);
            this.iface = iface; this.in = in; this.out = out;
            rumbleHoldMs = Math.max(0, holdMs);
        }

        @Override public boolean start() { return false; } // The receiver owns the physical link.
        @Override public void stop() { SteamControllerUsb.this.stop(); }
        @Override public void rumbleTriggers(short left, short right) { }

        void readLoop() {
            byte[] report = new byte[64];
            while (!stopped && !Thread.currentThread().isInterrupted()) {
                long before = SystemClock.uptimeMillis();
                int size;
                try {
                    size = connection.bulkTransfer(in, report, report.length, READ_TIMEOUT_MS);
                } catch (RuntimeException e) {
                    size = -1;
                }
                synchronized (this) {
                    if (stopped) return;
                    if (size > 0) handleReport(report, size);
                    if (connected && SystemClock.uptimeMillis() - lastStateMs >= STALE_STATE_MS) disconnect();
                }
                // Android exposes the same -1 for timeout, detach and I/O errors. A detached
                // receiver must not spin, even if its broadcast is delayed or absent.
                if (size <= 0 && SystemClock.uptimeMillis() - before < READ_TIMEOUT_MS / 2) {
                    try { Thread.sleep(READ_TIMEOUT_MS); } catch (InterruptedException e) { return; }
                }
            }
        }

        private void connect() {
            if (connected || stopped || SystemClock.uptimeMillis() < retryAtMs) return;
            connected = true;
            generation++;
            resetInputState();
            lastStateMs = SystemClock.uptimeMillis();
            notifyDeviceAdded();
            final int current = generation;
            outputHandler.post(() -> {
                synchronized (Slot.this) {
                    if (!connected || stopped || current != generation) return;
                }
                boolean ok = configure(Slot.this, true);
                synchronized (Slot.this) {
                    if (connected && current == generation && configureState != (ok ? 1 : 2)) logLinkSettings(Slot.this, ok);
                }
                if (!ok) writeFailed(current);
                else readAttributes(Slot.this, current);
            });
        }

        private int outputFailures;
        // A retained packet (an idempotent stop) is one strike however often it is
        // retried; its retry backs off so a stalled pipe is neither spun on nor
        // retired faster than the failure budget intends.
        private byte[] strikedReport;
        private int retryDelayMs = OUTPUT_RETRY_MS;

        private void disconnect() {
            generation++;
            outputs.clear();
            drainScheduled = false;
            configureState = 0;
            attributesQueried = false;
            repeatingRumble = null;
            outputFailures = 0;
            strikedReport = null;
            retryDelayMs = OUTPUT_RETRY_MS;
            outputHandler.removeCallbacks(rumbleTick);
            outputHandler.removeCallbacks(drain);
            // Release any touches the host still thinks are down (the BLE driver does the same).
            if (connected) releaseTouches();
            resetInputState();
            if (connected) {
                connected = false;
                notifyDeviceRemoved();
            }
        }

        private void handleReport(byte[] report, int size) {
            int id = report[0] & 0xff;
            if ((id == 0x79 || id == 0x46) && size >= 2) {
                if (report[1] == 1) disconnect();
                else if (report[1] == 2) connect();
                return;
            }
            int expected = id == 0x42 ? 54 : (id == 0x45 || id == 0x47) ? 46 : 0;
            if (expected != 0 && size >= expected) {
                connect(); // The puck may already be paired when Moonlight claims it.
                if (connected) {
                    lastStateMs = SystemClock.uptimeMillis();
                    handleState(report, 1, id == 0x47);
                }
            } else if (id == 0x43 && size >= 15 && connected) {
                handleBattery(report, 1);
            }
            // 0x44 haptic acknowledgements and 0x7B radio diagnostics are not game input.
        }

        synchronized void writeFailed(int current) {
            if (!stopped && connected && generation == current) {
                if (++outputFailures < OUTPUT_FAILURES_BEFORE_DISCONNECT) {
                    LimeLog.warning("Steam Controller USB: output failed on interface " + iface.getId()
                            + " (" + outputFailures + " of " + OUTPUT_FAILURES_BEFORE_DISCONNECT + ")");
                    return;
                }
                LimeLog.warning("Steam Controller USB: output keeps failing on interface " + iface.getId() + "; retiring the slot");
                retryAtMs = SystemClock.uptimeMillis() + KEEPALIVE_MS;
                disconnect();
            }
        }

        synchronized void writeSucceeded() {
            outputFailures = 0;
        }

        @Override public void rumble(short low, short high) {
            steamHaptic(new byte[]{(byte) 0x80, 0, 0, 0, (byte) low, (byte) (low >> 8),
                    0, (byte) high, (byte) (high >> 8), 0});
        }

        @Override public synchronized void steamHaptic(byte[] report) {
            if (stopped || !connected || report == null || report.length == 0 ||
                    report.length != outputSize(report[0] & 0xff)) return;
            int id = report[0] & 0xff;
            // Invalid side values must not consume the finite stop-priority queue.
            if ((id == 0x81 || id == 0x82) && (report[1] & 0xff) > 2) return;
            byte[] copy = Arrays.copyOf(report, report.length);
            if ((copy[0] & 0xff) == 0x80) {
                outputHandler.removeCallbacks(rumbleTick);
                repeatingRumble = stopKey(copy) >= 0 ? null : copy;
                rumbleDeadline = rumbleHoldMs == 0 ? Long.MAX_VALUE : SystemClock.uptimeMillis() + rumbleHoldMs;
                if (repeatingRumble != null) outputHandler.postDelayed(rumbleTick, 40);
            }
            enqueue(copy);
        }

        private final Runnable rumbleTick = new Runnable() {
            @Override public void run() {
                synchronized (Slot.this) {
                    if (stopped || !connected || repeatingRumble == null) return;
                    if (SystemClock.uptimeMillis() >= rumbleDeadline) {
                        repeatingRumble = null;
                        enqueue(new byte[]{(byte) 0x80, 0, 0, 0, 0, 0, 0, 0, 0, 0});
                    } else {
                        // A fresh instance each tick: enqueue() drops any queued 0x80, including
                        // the one a slow transfer is still writing, and that transfer's completion
                        // then removes its own instance; re-adding the same instance let that
                        // completion take the resend with it, leaving an ~80 ms gap past the
                        // firmware's rumble cutoff (A46-02).
                        enqueue(repeatingRumble.clone());
                        outputHandler.postDelayed(this, 40);
                    }
                }
            }
        };

        private void enqueue(byte[] report) {
            int stop = stopKey(report);
            // Latest rumble wins; duplicate stop packets need only one queue entry.
            outputs.removeIf(old -> ((report[0] & 0xff) == 0x80 && old[0] == report[0]) ||
                    (stop >= 0 && stopKey(old) == stop));
            if (outputs.size() >= MAX_OUTPUTS) {
                Iterator<byte[]> it = outputs.iterator();
                boolean evicted = false;
                while (it.hasNext()) {
                    if (stopKey(it.next()) < 0) { it.remove(); evicted = true; break; }
                }
                // Stops are never evicted by effects. Each stop key is coalesced above.
                if (!evicted) return;
            }
            outputs.addLast(report);
            if (!drainScheduled) {
                drainScheduled = true;
                outputHandler.post(drain);
            }
        }

        private final Runnable drain = new Runnable() {
            @Override public void run() {
                byte[] report;
                int current;
                synchronized (Slot.this) {
                    if (stopped || !connected || (report = outputs.peekFirst()) == null) {
                        drainScheduled = false;
                        return;
                    }
                    current = generation;
                }
                boolean success = writeOutput(Slot.this, report);
                synchronized (Slot.this) {
                    // A disconnect may have queued work for a new controller while this
                    // transfer was in flight. Its queue and failure budget belong to it.
                    if (generation != current) return;
                    if (success) {
                        writeSucceeded();
                        strikedReport = null;
                        retryDelayMs = OUTPUT_RETRY_MS;
                    } else if (report != strikedReport) {
                        // A retained stop that keeps failing is one strike, not one per retry.
                        strikedReport = report;
                        writeFailed(current);
                    }
                    // Retain an idempotent motor stop until it succeeds or the slot retires.
                    // Keeping it in the bounded queue also lets newer 0x80 rumble replace
                    // an obsolete stop. Failed effects are not replayed.
                    if (success || stopKey(report) < 0) outputs.remove(report);
                    if (!stopped && connected && generation == current && !outputs.isEmpty()) {
                        // One transfer per dispatch gives other puck slots a turn. Back off
                        // failures (40, 80, ... 1000 ms) so retries cannot spin or starve
                        // other slots; the keep-alive stays the liveness authority.
                        outputHandler.postDelayed(this, success ? 0 : retryDelayMs);
                        if (!success) retryDelayMs = Math.min(retryDelayMs * 2, KEEPALIVE_MS);
                    } else {
                        drainScheduled = false;
                    }
                }
            }
        };
    }
}
