# Steam Controller 2026 USB puck on Android

## Status and setup

The earlier Android build only opened the SC2026 Bluetooth service. This change adds
Valve's **28de:1304 puck** and **28de:1302 wired controller** to the built-in USB driver.
It does not support the 2015 Steam Controller receiver (28de:1142).

1. Pair the controller to the puck with Steam first. Pairing and firmware updates remain
   Steam operations; Moonlight consumes the already-paired connection.
2. Connect the puck to the Android device's USB host/OTG port (a powered hub may be needed).
   Enable **USB gamepad driver** and grant Android's USB permission when starting a stream.
   The **Steam Controller (2026) over Bluetooth** option may be off. Do not connect the
   same controller through both transports while testing.
3. On Windows, select Vibepollo's **Steam Controller (VHF)** profile (`vhf_sc26`). The host
   continues to emulate a wired SC2026; it does not need to emulate the receiver.
4. For native Steam Input, choose **Paddles 1–4 (Xbox Elite style)**, disable **Trackpads as DualShock touchpad halves**, and
   enable motion in the shared Steam Controller preferences. DS4 grip remapping and
   split pads remain available for compatibility profiles. Bluetooth's rumble-method
   preference does not change native USB output.
5. Test with the matching common-C, Android, host and virtual-driver changes. A host
   source submodule update alone does not replace an installed Windows driver.

## Coverage

| Feature | Implementation | Validation remaining |
|---|---|---|
| Puck input | Four independent interfaces/slots, discovered by first state or wireless-connect event | Real receiver descriptors, pairing and four-controller RF behavior |
| Buttons, sticks, analog triggers, four grips | Shared Bluetooth/USB mapping; distinct digital trigger clicks | Physical direction, calibration and trigger feel |
| Two pads | Independent position, touch, pressure and click bits | Both touched with only one clicked; edge/pressure range |
| Grip and stick capacitive touch | Explicit from first report with updated host/driver | Resting thumb, release while stick deflected, gyro activation bindings |
| IMU | Raw accelerometer and gyro with existing unit/axis mapping | Actual firmware settings and gyro orientation/drift |
| Battery | Charge state and percentage; USB service forwards both | Puck charging transitions and low-battery reports |
| Haptics | Native output reports 0x80..0x85, routed to the owning slot; bounded queue and stop preservation | Steam UI, game rumble, both pad effects and latency |
| Lifecycle | Empty puck creates no controller; per-slot disconnect/reconnect; detach/permission/rebind/stop handling | Android USB stack, suspend/resume, hub power loss |

This is **implemented and software-tested, not physical-puck verified**. Tests use fake
Android USB I/O around the production driver, and compile the real host adapter with the
real virtual-driver encoder. They cannot prove radio reliability, Android USB scheduling,
firmware acceptance or haptic feel. No latency advantage over Bluetooth has been measured.

## Protocol and compatibility

- Claim HID interfaces 2..5 on the puck, interface 0 for wired, using descriptor interface
  IDs rather than order. Leave receiver interfaces 0/1 alone. Each slot has a read loop;
  one bounded output worker serializes the shared USB connection's writes.
- Decode state 0x42 (54 bytes), 0x45 and timestamped-pad 0x47 (46 bytes); handle wireless
  0x79/0x46 and battery 0x43. Ignore truncated/unknown reports and haptic acknowledgements.
- Configure feature report 1 with settings opcode 0x87: lizard mode off and raw accel/gyro
  enabled. Refresh configuration every second, keep the firmware watchdog enabled, and
  restore lizard mode/stop haptics on normal shutdown. Silent input expires after 3 seconds.
- Use native interrupt OUT, or HID SET_REPORT(output) if no OUT endpoint exists. Validate
  exact 0x80..0x85 report sizes. Refresh held rumble every 40 ms until the configured hold
  limit (500 ms default); select unlimited only when the host reliably sends a stop.
- `LI_CCAP_SC26_BUTTONS=0x1000` declares authoritative per-pad/per-trigger click bits
  (button word bits 26..29), plus authoritative grip/stick touch from the first state.
  The shared mapper also retains the legacy combined pad-click flag for older hosts.
  The host sets an internal driver metadata bit 30 only for this capability and profile.
  No streaming packet or driver request changes size; old clients keep their heuristics.
- USB calibration is session-local: a puck slot can be occupied by another physical
  controller after reconnect. Bluetooth calibration remains keyed to its device address.

Existing pipeline limits remain: network analog triggers are 8-bit, the hardware quaternion
is not forwarded, and native PCM/stream haptic reports 0x86..0x89 are not supported. This
implements gameplay support and raw haptic families 0x80..0x85, not transparent USB tunneling.

## Hardware acceptance checklist

With the updated APK, host and installed driver, verify Steam's controller input screen
and a game for every row above. In particular: start with an empty puck; power on a paired
controller; touch both pads and click each separately; pull triggers slowly and confirm
physical end-stop clicks; rotate/tilt; rest and lift thumbs/grips; trigger both pad haptics.
Power-cycle one controller, unplug/replug the puck, stop/restart streaming, deny then grant
USB permission, and test Android suspend/resume. Held buttons must release on disconnect;
other slots must stay live; haptics must stop and lizard behavior should return after exit.

For failures, collect Android model/version, controller/puck firmware, USB VID/PID and
interface/endpoint descriptors, transport selection and relevant Moonlight log lines.

## Primary protocol sources

- [SDL Triton USB implementation](https://github.com/libsdl-org/SDL/blob/3e879b590728d2ad82dadee6efe591c7a9604c6a/src/joystick/hidapi/SDL_hidapi_steam_triton.c)
- [SDL controller report structures](https://github.com/libsdl-org/SDL/blob/3e879b590728d2ad82dadee6efe591c7a9604c6a/src/joystick/hidapi/steam/controller_structs.h)
- [SDL controller constants](https://github.com/libsdl-org/SDL/blob/3e879b590728d2ad82dadee6efe591c7a9604c6a/src/joystick/hidapi/steam/controller_constants.h)
- The project's existing wired SC2026 capture and descriptor contract in
  `libvirtualgamepad/captures/sc26-steam-interrupt.tsv` and `docs/SC26_USB_COMPATIBILITY.md`.

SDL sources were checked at commit `3e879b590728d2ad82dadee6efe591c7a9604c6a`. No copied SDL implementation is included;
input mapping is factored from this project's existing Bluetooth driver.
