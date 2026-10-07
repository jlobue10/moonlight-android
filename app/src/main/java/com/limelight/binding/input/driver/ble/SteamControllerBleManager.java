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
    private final boolean stickRim;
    private final Map<String, SteamControllerBle> drivers = new HashMap<>();
    private int nextDeviceId = BASE_DEVICE_ID;
    private BroadcastReceiver aclReceiver;

    public SteamControllerBleManager(Context context, UsbDriverListener listener, boolean motionEnabled,
                                     boolean splitPads, int gripsMode, int rumbleHoldMs, boolean stickRim) {
        this.context = context.getApplicationContext();
        this.listener = listener;
        this.motionEnabled = motionEnabled;
        this.splitPads = splitPads;
        this.gripsMode = gripsMode;
        this.rumbleHoldMs = rumbleHoldMs;
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
            return;
        }

        aclReceiver = new BroadcastReceiver() {
            @Override
            public void onReceive(Context c, Intent intent) {
                BluetoothDevice device = intent.getParcelableExtra(BluetoothDevice.EXTRA_DEVICE);
                if (device == null || !looksLikeSteamController(device)) {
                    return;
                }
                if (BluetoothDevice.ACTION_ACL_CONNECTED.equals(intent.getAction())) {
                    synchronized (SteamControllerBleManager.this) {
                        startDriver(device);
                    }
                }
            }
        };
        IntentFilter filter = new IntentFilter(BluetoothDevice.ACTION_ACL_CONNECTED);
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
            context.registerReceiver(aclReceiver, filter, Context.RECEIVER_EXPORTED);
        } else {
            context.registerReceiver(aclReceiver, filter);
        }
        LimeLog.info("Steam Controller BLE: watching " + drivers.size() + " bonded controller(s)");
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
        SteamControllerBle driver = new SteamControllerBle(nextDeviceId++, listener, context, device, motionEnabled, splitPads, gripsMode, rumbleHoldMs, stickRim);
        drivers.put(address, driver);
        if (!driver.start()) {
            drivers.remove(address);
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
