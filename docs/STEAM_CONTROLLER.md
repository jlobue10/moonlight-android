# 2026 Steam Controller over Bluetooth

## Why Android does not see it

The 2026 Steam Controller (Valve "Ibex"/"Triton", VID `0x28DE`, PID `0x1303` over Bluetooth LE,
`0x1302` wired, `0x1304` puck) is designed for Steam Input. Without Steam it exposes **no standard
HID gamepad**: all game input travels in a vendor-defined HID collection (usage page `0xFF00`), and
the firmware's default "lizard mode" only emulates a keyboard and a mouse. Android therefore pairs
it as a keyboard/mouse and never creates an `InputDevice` with gamepad sources, so Moonlight's
normal controller handling cannot see it. Apps also cannot read the HID service over GATT, because
the OS claims it.

Valve's vendor GATT service (`100f6c32-1735-4313-b402-38567131e5f3`) is not claimed by the OS, so an
app with `BLUETOOTH_CONNECT` can subscribe to the controller's state notifications and write its
commands directly. That is what the in-app driver does; no root, Shizuku or virtual gamepad is
needed, because the parsed state goes straight into Moonlight's own controller path.

## What the driver does

`binding/input/driver/ble/SteamControllerBleManager` runs while a stream is active (setting *Steam
Controller (2026) over Bluetooth*, default on). It looks through bonded devices whose name contains
"Steam Controller", starts a `SteamControllerBle` for each, and also catches controllers that connect
later (ACL connected broadcast). Each driver:

1. connects GATT (`TRANSPORT_LE`, high connection priority, MTU 100), discovers the Valve service,
   subscribes to every notify characteristic in `0x100f6c75..7a`, uses the first writable one in
   `0x100f6cb5..be` for commands;
2. sends `ID_CLEAR_DIGITAL_MAPPINGS` (0x81) and `ID_SET_SETTINGS_VALUES` (0x87) with
   `SETTING_LIZARD_MODE = 0` and `SETTING_STEAM_WATCHDOG_ENABLE = 0`, then `SETTING_IMU_MODE =
   RAW_ACCEL | RAW_GYRO` (0x18) when motion is enabled; re-sends lizard-off once a second;
3. parses the 0x45 state report (BLE delivers it without the report-id byte, so every documented
   USB offset is one less) into Moonlight's `ControllerPacket` flags, triggers, sticks, two
   touchpads (`LiSendControllerTouchEvent2`, pads 0 and 1), gyro/accel and battery;
4. announces itself as `LI_CTYPE_STEAM` with `ANALOG_TRIGGERS | RUMBLE | TOUCHPAD | DUAL_TOUCHPAD |
   BATTERY_STATE` (+ `GYRO | ACCEL`); rumble is done with `ID_TRIGGER_HAPTIC_PULSE` (0x8F) pulse
   trains (~160 Hz, magnitude as duty cycle; firmware side 1 = left/low, 0 = right/high) that are
   re-issued every 100 ms while the host's rumble is non-zero, the way Steam and the kernel driver
   drive this controller, and stopped by a zero-repeat pulse;
5. on stop restores the default digital mappings and settings so the controller works as a
   keyboard/mouse for the rest of the system again.

### Button mapping

| Controller | Moonlight flag |
|---|---|
| A B X Y | A B X Y |
| left pad d-pad directions (firmware bits) | UP DOWN LEFT RIGHT |
| LB RB | LB RB |
| Menu / View | PLAY / BACK |
| stick clicks | LS_CLK / RS_CLK |
| Steam | SPECIAL_BUTTON |
| Quick access (…) | MISC |
| R4 / L4 / R5 / L5 grips | PADDLE1 / PADDLE2 / PADDLE3 / PADDLE4 |
| pad clicks | TOUCHPAD |

Triggers: 0..32767 → 0..1. Sticks: signed, up positive (same as the XInput drivers). Pads: x =
raw/65536 + 0.5, y = −raw/65536 + 0.5, pressure/32768. IMU: gyro ±2000 °/s, accel ±2 g, axis order
(x, z, −y) as in SDL's Triton driver.

## Host side

The host decides what it emulates from the arrival event. In the Sunshine family's default
`gamepad = auto` mode a client that reports touchpad or motion capabilities gets a **DualShock 4**,
which is why Vibepollo shows the controller as a PS4 pad: that is the only emulated pad with a
touchpad and gyro, and Steam Input handles it well. Consequences, and the settings that address them:

- A DualShock 4 has one touchpad, which Steam Input splits into left and right halves. *Trackpads as
  DualShock touchpad halves* (default on) therefore sends the left pad as finger 0 in the left half
  and the right pad as finger 1 in the right half of touchpad 0, so Steam sees "left pad"/"right pad".
  Turn it off only for a host that understands `LI_CCAP_DUAL_TOUCHPAD` (common-c drops touchpad 1
  otherwise).
- A DualShock 4 has no paddles, so the host drops the grips. *Grip buttons* lets you choose
  "DualShock extras" (L4 Share, R4 touchpad click, L5 L3, R5 R3), which Steam Input can rebind, or
  keep paddles for hosts that emulate a pad with them.
- The Steam button arrives as the PS button and opens Big Picture.
- To get an Xbox pad instead, set `gamepad = x360` on the host; touchpads and gyro are then dropped.

## Sources

Protocol: Linux `drivers/hid/hid-steam.c` (Ibex report table, command and setting ids), SDL3
`SDL_hidapi_steam_triton.c` and `steam/controller_constants.h` (settings numbering, IMU scales,
pad normalisation). GATT characteristic ranges and the Android-specific handling (OS claims the HID
service, state notifications lack the report id, MTU 100): the SteamController-Android project
(MIT), which uses Shizuku to create a system-wide virtual gamepad instead. All code here is
original; these were used as documentation.

## Seen on the headset (fork.6)

- Connects, announces as `LI_CTYPE_STEAM`, host (Vibepollo, `gamepad=auto`) emulates a DualShock 4;
  Steam button and rumble work; sticks were vertically inverted (fixed in fork.7).
- GATT MTU negotiates to 67 on the Galaxy XR (the state report fits).
- A second bonded controller that is off or out of range fails `connectGatt` with status 133 after
  about 30 s; the driver now stops retrying such a device and waits for the system's
  ACL-connected broadcast instead.

## Unverified until tried on the headset

- That the Galaxy XR's Bluetooth stack exposes the vendor service to apps (it does on phones).
- Lizard-mode disable via 0x81 + settings (the Android project sends 0x85 instead; the kernel and
  SDL use the sequence implemented here). If keyboard/mouse events keep arriving while streaming,
  try adding a 0x85 write to the keep-alive.
- Battery charge-state semantics (byte 0 of the battery characteristic; non-zero is treated as
  charging).
- Rumble feel; stop is an explicit zero-repeat pulse.
- Rumble hold limit (fork.14): Vibepollo 2.0.0 presenting the controller as a DualSense forwards
  Steam's menu haptics as rumble but was observed never to send the matching "motors off" (stream
  log: `Rumble on gamepad 0: 6600 6600`, `7300 8000`, ... and no `0000 0000`), which with an
  unbounded pulse train left the pads buzzing until the next event. Settings → Input → "Rumble hold
  limit" (default 0.5 s, or "Until the host says stop" for the old behaviour) bounds how long one
  host event may rumble. The host-side cause is still open (driver `apply_ds5_output` ignores
  `HAPTICS_SELECT`-only reports? Steam's stop path?); narrowing test = compare `vhf_ds4` and `ds4`.
- Whether the IMU setting takes effect over BLE (otherwise motion stays at zero; turn the motion
  setting off).
