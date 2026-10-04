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
   BATTERY_STATE` (+ `GYRO | ACCEL`); rumble is done with `ID_TRIGGER_HAPTIC_PULSE` (0x8F) pulses on
   the left (low) and right (high) side;
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

The host decides what it emulates from the arrival event. Sunshine/Apollo/Vibepollo treat unknown
types as Xbox; hosts that know `LI_CTYPE_STEAM` (added to moonlight-common-c in 2026) can present a
Steam-style pad with trackpads to Steam Input. Either way buttons, sticks, triggers, grips and rumble
work as an Xbox-class pad; the touchpads and motion only do something on hosts that forward them
(`LI_FF_CONTROLLER_TOUCH_EVENTS`).

## Sources

Protocol: Linux `drivers/hid/hid-steam.c` (Ibex report table, command and setting ids), SDL3
`SDL_hidapi_steam_triton.c` and `steam/controller_constants.h` (settings numbering, IMU scales,
pad normalisation). GATT characteristic ranges and the Android-specific handling (OS claims the HID
service, state notifications lack the report id, MTU 100): the SteamController-Android project
(MIT), which uses Shizuku to create a system-wide virtual gamepad instead. All code here is
original; these were used as documentation.

## Unverified until tried on the headset

- That the Galaxy XR's Bluetooth stack exposes the vendor service to apps (it does on phones).
- Lizard-mode disable via 0x81 + settings (the Android project sends 0x85 instead; the kernel and
  SDL use the sequence implemented here). If keyboard/mouse events keep arriving while streaming,
  try adding a 0x85 write to the keep-alive.
- Battery charge-state semantics (byte 0 of the battery characteristic; non-zero is treated as
  charging).
- Rumble feel; stop is an explicit zero-repeat pulse.
- Whether the IMU setting takes effect over BLE (otherwise motion stays at zero; turn the motion
  setting off).
