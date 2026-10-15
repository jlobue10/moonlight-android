package com.limelight.binding.input.driver.ble;

import android.Manifest;
import android.annotation.SuppressLint;
import android.bluetooth.BluetoothAdapter;
import android.bluetooth.BluetoothDevice;
import android.bluetooth.BluetoothManager;
import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;
import android.content.IntentFilter;
import android.content.pm.PackageManager;
import android.os.Build;

import androidx.core.content.ContextCompat;

import com.limelight.LimeLog;
import com.limelight.binding.input.driver.UsbDriverListener;

import java.util.HashMap;
import java.util.Locale;
import java.util.Map;
import java.util.Set;

/**
 * Finds bonded 2026 Steam Controllers and runs a {@link SteamControllerBle} driver for each one
 * while a stream is active. Controllers that connect to the system later (switched on mid-stream)
 * are picked up through the ACL connection broadcast.
 */
public final class SteamControllerBleManager {

    private static final String[] NAME_HINTS = {"steam controller", "steam ctrl", "steamcontroller"};
    /** Driver ids live above anything the USB driver service hands out. */
    private static final int BASE_DEVICE_ID = 0x5C00;

    private final Context context;
    private final UsbDriverListener listener;
    private final boolean motionEnabled;
    private final boolean splitPads;
    private final int gripsMode;
    private final int rumbleHoldMs;
    private final int rumbleMethod;
    private final int hapticBoostDb;
    private final int pulseScalePercent;
    private final int masterGainDb;
    private final boolean hapticSelfTest;
    private final boolean stickRim;
    private final Map<String, SteamControllerBle> drivers = new HashMap<>();
    private int nextDeviceId = BASE_DEVICE_ID;
    private BroadcastReceiver aclReceiver;

    public SteamControllerBleManager(Context context, UsbDriverListener listener, boolean motionEnabled,
                                     boolean splitPads, int gripsMode, int rumbleHoldMs, int rumbleMethod,
                                     int hapticBoostDb, int pulseScalePercent, int masterGainDb, boolean hapticSelfTest,
                                     boolean stickRim) {
        this.context = context.getApplicationContext();
        this.listener = listener;
        this.motionEnabled = motionEnabled;
        this.splitPads = splitPads;
        this.gripsMode = gripsMode;
        this.rumbleHoldMs = rumbleHoldMs;
        this.rumbleMethod = rumbleMethod;
        this.hapticBoostDb = hapticBoostDb;
        this.pulseScalePercent = pulseScalePercent;
        this.masterGainDb = masterGainDb;
        this.hapticSelfTest = hapticSelfTest;
        this.stickRim = stickRim;
    }

    /** Maps the steam_controller_rumble_hold preference value (milliseconds, "0" = unlimited). */
    public static int rumbleHoldFromPref(String value) {
        try {
            return Math.max(0, Integer.parseInt(value));
        } catch (NumberFormatException e) {
            return 500;
        }
    }

    /** Maps the steam_controller_rumble_method preference value to a SteamControllerBle.RUMBLE_METHOD_* constant. */
    public static int rumbleMethodFromPref(String value) {
        if ("pulse".equals(value)) return SteamControllerBle.RUMBLE_METHOD_PULSES;
        return SteamControllerBle.RUMBLE_METHOD_RUMBLE_CMD;
    }

    private static int intPref(String value, int fallback, int min, int max) {
        try {
            return Math.max(min, Math.min(max, Integer.parseInt(value)));
        } catch (NumberFormatException e) {
            return fallback;
        }
    }

    /** Maps the steam_controller_haptic_boost preference value (dB added to Steam's 0x82 gain byte). */
    public static int hapticBoostFromPref(String value) {
        return intPref(value, 0, 0, 30);
    }

    /** Maps the steam_controller_haptic_pulse_scale preference value (percent of Steam's 0x81 pulse on-time). */
    public static int pulseScaleFromPref(String value) {
        return intPref(value, 100, 100, 400);
    }

    /** Maps the steam_controller_haptic_master_gain preference value (dB written as setting 76; "off" = not written). */
    public static int masterGainFromPref(String value) {
        return intPref(value, 0, 0, 24);
    }

    /** Maps the steam_controller_grips preference value to a SteamControllerBle.GRIPS_* constant. */
    public static int gripsModeFromPref(String value) {
        if ("ds4".equals(value)) return SteamControllerBle.GRIPS_DS4;
        if ("off".equals(value)) return SteamControllerBle.GRIPS_OFF;
        return SteamControllerBle.GRIPS_PADDLES;
    }

    /** The runtime permission this feature needs on the current Android version. */
    public static String requiredPermission() {
        return Build.VERSION.SDK_INT >= Build.VERSION_CODES.S
                ? Manifest.permission.BLUETOOTH_CONNECT
                : Manifest.permission.BLUETOOTH;
    }

    public static boolean hasPermission(Context context) {
        return ContextCompat.checkSelfPermission(context, requiredPermission()) == PackageManager.PERMISSION_GRANTED;
    }

    @SuppressLint("MissingPermission")
    public static boolean looksLikeSteamController(BluetoothDevice device) {
        try {
            String name = device.getName();
            if (name == null) {
                return false;
            }
            String lower = name.toLowerCase(Locale.ROOT);
            for (String hint : NAME_HINTS) {
                if (lower.contains(hint)) {
                    return true;
                }
            }
        } catch (SecurityException ignored) {
        }
        return false;
    }

    @SuppressLint("MissingPermission")
    public synchronized void start() {
        if (!hasPermission(context)) {
            LimeLog.warning("Steam Controller BLE: Bluetooth permission not granted; driver disabled");
            return;
        }

        // Register before enumerating: with Bluetooth off (or the bonded list refused) at
        // stream start, a controller switched on later, or the adapter turned on, must still
        // be picked up. The receiver used to be registered only after a successful enumeration.
        if (aclReceiver == null) {
            aclReceiver = new BroadcastReceiver() {
                @Override
                public void onReceive(Context c, Intent intent) {
                    if (BluetoothAdapter.ACTION_STATE_CHANGED.equals(intent.getAction())) {
                        if (intent.getIntExtra(BluetoothAdapter.EXTRA_STATE, -1) == BluetoothAdapter.STATE_ON) {
                            synchronized (SteamControllerBleManager.this) {
                                if (aclReceiver != null) {
                                    enumerateBonded();
                                }
                            }
                        }
                        return;
                    }
                    BluetoothDevice device = intent.getParcelableExtra(BluetoothDevice.EXTRA_DEVICE);
                    if (device == null || !looksLikeSteamController(device)) {
                        return;
                    }
                    if (BluetoothDevice.ACTION_ACL_CONNECTED.equals(intent.getAction())) {
                        synchronized (SteamControllerBleManager.this) {
                            if (aclReceiver != null) {
                                startDriver(device);
                            }
                        }
                    }
                }
            };
            IntentFilter filter = new IntentFilter(BluetoothDevice.ACTION_ACL_CONNECTED);
            filter.addAction(BluetoothAdapter.ACTION_STATE_CHANGED);
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
                context.registerReceiver(aclReceiver, filter, Context.RECEIVER_EXPORTED);
            } else {
                context.registerReceiver(aclReceiver, filter);
            }
        }

        enumerateBonded();
        LimeLog.info("Steam Controller BLE: watching " + drivers.size() + " bonded controller(s)");
    }

    /** Starts a driver for every bonded controller the adapter can list right now; best effort. */
    @SuppressLint("MissingPermission")
    private void enumerateBonded() {
        BluetoothManager manager = (BluetoothManager) context.getSystemService(Context.BLUETOOTH_SERVICE);
        BluetoothAdapter adapter = manager != null ? manager.getAdapter() : null;
        if (adapter == null || !adapter.isEnabled()) {
            LimeLog.info("Steam Controller BLE: Bluetooth is off or unavailable");
            return;
        }
        try {
            Set<BluetoothDevice> bonded = adapter.getBondedDevices();
            if (bonded != null) {
                for (BluetoothDevice device : bonded) {
                    if (looksLikeSteamController(device)) {
                        startDriver(device);
                    }
                }
            }
        } catch (SecurityException e) {
            LimeLog.warning("Steam Controller BLE: cannot list bonded devices: " + e.getMessage());
        }
    }

    private void startDriver(BluetoothDevice device) {
        String address = device.getAddress();
        SteamControllerBle existing = drivers.get(address);
        if (existing != null) {
            if (!existing.isLinkUp()) {
                existing.start();   // it came (back) into range: the system just connected to it
            }
            return;
        }
        SteamControllerBle driver = new SteamControllerBle(nextDeviceId++, listener, context, device, motionEnabled, splitPads, gripsMode, rumbleHoldMs, rumbleMethod, hapticBoostDb, pulseScalePercent, masterGainDb, hapticSelfTest, stickRim);
        drivers.put(address, driver);
        if (!driver.start()) {
            drivers.remove(address);
            driver.stop();
        }
    }

    public synchronized void stop() {
        if (aclReceiver != null) {
            try {
                context.unregisterReceiver(aclReceiver);
            } catch (IllegalArgumentException ignored) {
            }
            aclReceiver = null;
        }
        for (SteamControllerBle driver : drivers.values()) {
            driver.stop();
        }
        drivers.clear();
    }
}
