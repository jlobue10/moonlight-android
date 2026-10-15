# 2026 Steam Controller

The built-in driver now also supports the **USB puck on the Android client**, and direct USB.
See [puck setup, feature coverage and hardware validation](SC2026_USB_PUCK.md). The Bluetooth
transport described below shares its input decoder and settings with USB.

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
   uses feature characteristic `0x100f6c34` for commands, and decodes state from characteristic
   `0x100f6c7c` (report 0x47) or `0x100f6c7a` (report 0x45), plus battery `0x100f6c78` when available.
   Report 0x47 is preferred when both exist. Every notify characteristic in `0x100f6c75..7c` is
   subscribed so command acknowledgements stay visible in the log, but only the selected state
   characteristic is decoded. Setup requires a successful state subscription; battery success
   alone does not announce a controller. The HID output reports 0x80..0x89 each have their own
   writable characteristic at `0x100f6cb5` + (id − 0x80) (B5 = 0x80 rumble, B6 = 0x81 pulse,
   B7 = 0x82 haptic command, ...), mirroring the input reports on `0x100f6c75` + (id − 0x40)
   (78 = 0x43 battery, 79 = 0x44 haptic acknowledgement); none of them is a feature-command
   endpoint. The driver logs which output characteristics the firmware offers, with their
   properties. Firmware that does not expose these endpoints gets the earlier behaviour (logged
   as a warning): the first writable characteristic in `0x100f6cb5..be` takes commands and any
   45-byte report on the notify range is decoded as 0x45;
2. sends `ID_CLEAR_DIGITAL_MAPPINGS` (0x81) and `ID_SET_SETTINGS_VALUES` (0x87) with
   `SETTING_LIZARD_MODE = 0` and `SETTING_STEAM_WATCHDOG_ENABLE = 0`, then `SETTING_IMU_MODE =
   RAW_ACCEL | RAW_GYRO` (0x18) when motion is enabled; re-sends lizard-off once a second;
3. parses the selected 0x45 or 0x47 state report (BLE delivers it without the report-id byte).
   Both payloads are 45 bytes: 0x47 adds a two-byte timestamp before the pads and shortens the
   IMU timestamp by two bytes. Only the selected state characteristic is decoded into Moonlight's `ControllerPacket` flags, triggers, sticks, two
   touchpads (`LiSendControllerTouchEvent2`, pads 0 and 1), gyro/accel and battery;
4. announces itself as `LI_CTYPE_STEAM` with `ANALOG_TRIGGERS | RUMBLE | TOUCHPAD | DUAL_TOUCHPAD |
   BATTERY_STATE | GRIP_SENSE | STICK_TOUCH | STEAM_HAPTIC` (+ `GYRO | ACCEL`). A Vibepollo host
   presenting the Steam Controller profile sends Steam's own haptic output reports
   (`LI_CCAP_STEAM_HAPTIC`, control packet 0x5504): 0x80 rumble, 0x81 pulse, 0x82 command, 0x83
   LFO tone, 0x84 log sweep, 0x85 script. Since fork.97 each is written as-is, minus its report
   id, to that report's own output characteristic (B5 = 0x80, B6 = 0x81, ...), the way the USB
   transport writes it to the wired unit; Steam's trackpad clicks are 0x81 pulse pairs and 0x82
   commands. A 0x80 is re-sent every 40 ms against the firmware's ~50 ms safety timeout until the
   host's zero or the hold limit. Plain host rumble becomes a 0x80 report (or, with the "Rumble
   method" setting, 0x81 pulse trains at ~160 Hz with the magnitude as duty cycle; side 1 = left/low,
   0 = right/high, re-issued every 100 ms). Fork.27/28 instead converted the reports into the
   firmware's feature messages on the command characteristic (0x80 → `ID_TRIGGER_RUMBLE_CMD`
   0xEB, 0x81 → `ID_TRIGGER_HAPTIC_PULSE` 0x8F framed as hid-steam does, 0x82 →
   `ID_TRIGGER_HAPTIC_CMD` 0xEA); the Ibex firmware refuses 0x8F and 0xEA over BLE (see the
   follow-up section), so that conversion is now only the fallback for a link without the output
   characteristics, or whose output characteristics refuse the report (first retried once with
   the report id, then the feature form for the rest of the link; each switch is logged). Writes
   go one at a time (the stack takes one GATT operation);
   a refused write is retried after 50 ms, a completion that never comes is cleared after 2 s,
   haptic commands go without response, a stall never evicts a motors-off, the keep-alive
   re-sends the whole settings triple, and `stop()` restores lizard mode through the same queue
   before closing the link (fork.28);
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
- Battery charge-state values on a real unit (byte 0 of the battery characteristic is mapped per
  the firmware's EChargeState: 1 discharging, 2/3 charging, 4 full, 0 unknown; not yet observed
  on battery).
- Rumble feel of the 0xEB rumble command and the 0xEA click/tick replay over BLE (fork.27; the
  constants are documented for the Deck's haptic engine, which the Triton's output reports mirror
  field for field). If nothing is felt, switch "Rumble method" to pulse trains and report.
- Rumble hold limit (fork.14): Vibepollo 2.0.0 presenting the controller as a DualSense forwards
  Steam's menu haptics as rumble but was observed never to send the matching "motors off" (stream
  log: `Rumble on gamepad 0: 6600 6600`, `7300 8000`, ... and no `0000 0000`), which with an
  unbounded pulse train left the pads buzzing until the next event. Settings → Input → "Rumble hold
  limit" (default 0.5 s, or "Until the host says stop" for the old behaviour) bounds how long one
  host event may rumble. The host-side cause is still open (driver `apply_ds5_output` ignores
  `HAPTICS_SELECT`-only reports? Steam's stop path?); narrowing test = compare `vhf_ds4` and `ds4`.
- Whether the IMU setting takes effect over BLE (otherwise motion stays at zero; turn the motion
  setting off).

## Grip sense (fork.19)

The controller's capacitive grip sensors arrive as bits 28 (right) and 29 (left) of the
button word. Moonlight has no event for "held", so this fork carries them as two extension
button flags, `LEFT_GRIP_TOUCH_FLAG` 0x400000 and `RIGHT_GRIP_TOUCH_FLAG` 0x800000, and
announces them with `LI_CCAP_GRIP_SENSE` (0x200). Sunshine-family hosts receive the upper
16 flag bits (`buttonFlags2`) and ignore ones they do not know; Vibepollo with the Steam
Controller profile forwards them to the virtual device, where Steam reads them as the real
grip sensors ("gyro on grip"). Hosts without the extension fall back to the driver's own
guess (grips held while motion is streaming).

## Stick extent diagnostics (fork.20)

Steam's "move the left stick in a full circle" calibration step stalls on the virtual
controller (beta.104). The client passes the raw stick through (`s16 / 32767`, a 5 % centre
dead zone, no rescaling), so to see what the controller itself delivers over BLE the driver
now logs, every 10 s while a stick is away from centre, each stick's peak magnitude and how
many of 16 angular sectors it visited: `Steam Controller BLE: stick extents: left max 0.98 in
16/16 sectors, ...`. A peak well under 1.0 or missing sectors means the BLE report never
reaches the rim and Steam's step cannot complete through any host.

## Stick rim calibration (fork.21)

The fork.20 diagnostics settled where the stall is. Steam's whole controller test passes
against the virtual device alone (`probe_sc26_usb --hold` drives every step, 2026-10-07), the
host forwards the four stick values untouched, and the client only applies a centre dead
zone, so the only thing that differs over the stream is the stick data the controller sends
over BLE. On hardware it peaked at a magnitude of 1.18 across all 16 sectors: the BLE stick is
not shaped like the wired report (it overshoots the unit circle on diagonals, so it cannot
reach the rim evenly in every direction), and Steam's "full circle" step waits for a rim it
never sees.

`Scale sticks to the rim` (on by default, under the Steam Controller settings) divides each
axis by the largest deflection seen on it (floor 0.75, so a full push reaches the rim in every
direction after the first circle), clamps the result to the unit circle, and remembers the
learned extents per controller address. The 10 s diagnostics line now reports the raw values
per axis and per compass point, plus the learned extents, so the BLE stick's real shape is
visible in the stream log:

```
Steam Controller BLE: stick extents (raw): left max 1.167 (|x| 0.83 |y| 0.84) in 16/16 sectors,
  peaks W 0.83 NW 1.17 N 0.84 ..., right ...; rim calibration on, extents left 0.830/0.840 right ...
```

## Bluetooth hardware regression follow-up

The fork.92 Galaxy XR test recorded successful MTU negotiation and notifications, but setup
writes to `100f6cb5` failed with GATT status 7. Fork.93 added diagnostics and a write-type
fallback; it still selected the rumble-output endpoint for feature commands. The endpoint
and report mappings above follow SDL's Android Steam Controller implementation:
[HIDDeviceBLESteamController.java](https://github.com/libsdl-org/SDL/blob/f6ffa69890a21921ad5199505fbd7dc4333cb6d0/android-project/app/src/main/java/org/libsdl/app/HIDDeviceBLESteamController.java).
Feature writes start with command and payload-length bytes (including zero length for
clear/restore commands), without a HID report ID or the older controller's `0xC0` prefix.

Regression tests exercise the production BLE driver with a realistic service map, both state
layouts, timestamped pad coordinates, failed state subscriptions, and unrelated reports.
They cannot establish physical Bluetooth compatibility. Retest on the affected device:

1. Confirm the log selects command characteristic `100f6c34` and state `100f6c7a` or `100f6c7c`.
2. Confirm setup/keepalive writes complete without repeated errors, then test buttons,
   sticks, triggers, both pads and motion in a stream.
3. Disconnect/reconnect during streaming and verify input resumes; stop streaming and verify
   default controller behavior returns.
4. Test rumble/haptics separately. Existing feature-command haptic conversion remains in place;
   physical haptic behavior is not established by the input-path fix.

### fork.97: haptics over the output characteristics

The fork.96 Galaxy XR test (stream_18, 2026-10-13) had every input working but no haptics at
all: no trackpad clicks and nothing during Steam's controller test. The stream log shows why.
Every replayed Steam haptic report was refused by the controller on the feature characteristic:

```
Steam Controller BLE: write failed, status 4 (cmd 0x8f, 10 bytes) (with response)
Steam Controller BLE: write failed, status 4 (cmd 0xea, 21 bytes) (with response)
```

GATT status 4 is the ATT "invalid PDU" error from the peer: the Ibex firmware does not accept
`ID_TRIGGER_HAPTIC_PULSE` (0x8F) or `ID_TRIGGER_HAPTIC_CMD` (0xEA) as feature commands over
BLE (they are Deck-era haptic-engine messages; the setup commands 0x81/0x87 on the same
characteristic are accepted). Steam's trackpad click is exactly those two reports (an 0x81
pulse pair plus an 0x82 command, `libvirtualgamepad/docs/SC26_USB_COMPATIBILITY.md`), so the
conversion could never have produced a click.

The wired unit takes Steam's reports as HID output reports, and the Valve service has one
writable characteristic per output report id, `0x100f6cb5` + (id − 0x80), just as it has one
notify characteristic per input report id, `0x100f6c75` + (id − 0x40) (7A = 0x45, 7C = 0x47,
78 = 0x43 battery, 79 = 0x44 haptic acknowledgement). Fork.97 therefore writes each report to
its own characteristic, without the report id (the notifications omit theirs, and SDL's BLE
driver for the first controller strips it the same way), and keeps the feature conversion only
as a fallback. What to look for on the next device test:

1. At connect: `Steam Controller BLE: output report characteristics (properties): 0x80 (0x..),
   0x81 (0x..), ...`. Properties 0xc = write and write-without-response, 0x8 = write only.
2. Touching a pad in Steam's UI should click, and the log should stay free of `write failed`
   lines. A `output report refused without its id; retrying with the report id` line means the
   firmware wants the id byte on the air (the driver switches for the rest of the link; report
   it, and the default can change); a second refusal falls back to the feature commands, which
   we know do not work, so haptics would stay silent. (Superseded in fork.116: the retry with
   the id and the feature fallback are gone; a refusal is retried id-less once, then dropped,
   and the 10 s summary shows each report's verdict.)
3. 0x44 acknowledgements now show as "n-byte report on 0x100f6c79" only when the short-report log
   is still within its first five lines; they are expected after each zero-repeat pulse.

### fork.98: haptic diagnostics and per-report fallback

fork.97 on the headset (stream_19, 2026-10-13): all ten output characteristics are offered
(`0x80..0x89`, properties 0xe = read, write, write-without-response), no write fails, and
something is felt, but weakly, and nothing while a finger slides over a pad. The USB capture of
the wired unit (`libvirtualgamepad/captures/sc26-steam-interrupt.tsv`) shows what Steam sends for
those two things: a finger sliding over a pad in Steam's UI produces bursts of single 400 µs pulses
`81 <side> 90 01 00 00 01 00` (20 to 200 ms apart), and a pad click produces `82 <side> 02 f2`
(click, −14 dB) runs; the firmware answers each zero-repeat `81 <side> 00..` stop with a
`44 <actuator> 02 ..` report (actuator 4 = right pad, 3 = left pad). Haptic writes go without
response, so a refused report could not show in the fork.97 log. fork.98 adds, per link:

- the first write of each output report id goes with a response and its verdict is logged
  (`output report 0x81 accepted by the controller without its report id`, or `refused (status n)`);
  later writes of that id go without response as before;
- the refusal ladder (retry with the report id, then the feature-command form; replaced in
  fork.116 by one id-less retry) is per report id, so a refused 0x82 cannot take 0x81 or 0x80
  down with it;
- the first 24 host haptic reports are logged one by one (`host haptic report #n at +t ms: ..`),
  and every 10 s a summary: `haptics, last 10 s: host 0x81 x12 (4 stops, 0 ignored); written
  0x81 x12; evicted 0; acks 4 (last 04 02 00 00 00)`. "host" counts what Steam sent through
  the stream, "written" what completed on the link, "acks" the 0x44 reports on `0x100f6c79`.

How to read the next log: no `host 0x81` entries while sliding a finger means Steam is not
generating the detent pulses for the streamed controller (a host/Steam-side question: the pad
data the virtual device reports, or Steam's per-controller haptic settings, which are keyed by
serial and start at defaults for the virtual unit); `host 0x81` present but `written` short or
`evicted` non-zero means the link cannot keep up; `written` matching `host` with no acks and
nothing felt means the firmware takes the report but does not act on it over BLE, and the
report-id framing or the gain path is the next suspect.

### fork.111: haptic framing and gain knobs

fork.110 on the headset (stream_21, 2026-10-14) carried Steam's settings writes through
(`host settings 0x87: 7=7 8=7 82=3 ...`, every block the USB capture shows), and the haptics
felt no different, so the settings were never the lever. The remaining difference between the
wired capture and the stream is one byte: Steam's 0x82 commands reach the wired unit as
`82 <side> 01 FD` (tick, −3 dB) and `82 <side> 02 F2` (click, −14 dB), but the stream_20 log
shows `82 01 01 00`. Every hop copies the report verbatim (driver, Vibepollo, common-c, this
client), so that 0 comes from Steam itself, and Steam never reads settings back from the device,
so it is Steam-side state (its per-controller haptics intensity is the likely source). The other
unverified piece is the id-less framing on the output characteristics, which only a lack of GATT
errors has validated so far. fork.111 adds two diagnostic preferences and better logging:

- **Bluetooth haptic report framing**: `output` (default, without the report id) or `with_id`
  (every output report carries its id from the first write; a refusal with the id falls back to
  the feature commands, which the firmware refuses, so haptics would go silent for that id).
- **Bluetooth haptic gain**: `host` (default, as sent), `usb` (ticks −3 dB, clicks −14 dB, as the
  wired capture) or `plus6` (+6 dB). Only the gain byte of a 0x82 tick or click is rewritten;
  0x82 stops, 0x81 pulses and 0x80 rumble are untouched. The 10 s summary counts the rewrites
  (`0x82 gain overridden x59`).
- Host haptic samples are logged per report id (12 each) instead of 24 in total, so the 0x82
  commands always show (`host haptic report #n at +t ms: 82 01 01 00`), and the link logs its
  configuration at setup (`haptic path: output reports without their id; 0x82 gain: ...`).

How to use it: run Steam's controller test once per setting and compare feel. If `usb` or `plus6`
changes the pads, the gain byte is the lever and the question moves to why Steam sends 0 for the
streamed controller (open Steam's controller settings for it and change its haptics intensity
while streaming: the sampled 0x82 bytes show whether that is the knob). If `with_id` changes the
feel, the default framing was wrong. If nothing changes, the firmware acts on these reports the
same way over BLE and the remaining difference is the round trip each tick takes.

### fork.112: what fork.111 showed, and three haptic knobs

fork.111 on the headset (stream_22, 2026-10-15), gain forced to +6 dB: the clicks Steam sent
through the stream were `82 00 02 f2`, byte for byte what the wired unit gets, and the ticks
`82 01 01 00` (the wired unit gets -3 dB), so the host is not the difference. But +6 dB on both
was *noticeably stronger*, so the firmware's gain byte works over BLE and the baseline is simply
too low for this transport. With the report id on the output characteristics nothing was felt at
all (the writes were accepted): the id-less framing is right, and that knob is gone.

fork.112 replaces the two diagnostics with three knobs, all under the Steam Controller settings:

- **Bluetooth pad click boost** (`steam_controller_haptic_boost`: 0, 6, 12, 20 dB): added to the
  signed gain byte of Steam's 0x82 tick and click commands, clamped to the firmware's +6 dB
  ceiling (hid-steam documents -24..+6). +6 keeps Steam's click/tick balance (-8 / +6), +20 puts
  both at the ceiling, which is what fork.111's "+6 dB" setting did. Stops are untouched.
- **Bluetooth pad slide pulse length** (`steam_controller_haptic_pulse_scale`: 100, 150, 200,
  300 %): Steam's slide texture is single 400 µs 0x81 pulses with no gain field, so the only way
  to make them stronger is to lengthen the on-time. Zero-repeat and zero-duration stops stay.
- **Bluetooth haptic master gain** (`steam_controller_haptic_master_gain`: off, 6, 12 dB): writes
  `SETTING_HAPTIC_MASTER_GAIN_DB` (76) in the link's own setup command (and so after every host
  reset and keep-alive). Steam never writes it; if the firmware honours it over BLE, everything
  including the slide pulses gets stronger and the two knobs above become unnecessary.

The link logs its configuration at setup (`haptics: 0x82 gain boost +6 dB (ceiling +6 dB), 0x81
pulse on-time x2.0, master gain setting 76=+6 dB`) and the 10 s summary counts the rewritten
reports (`boosted x52`). Suggested order on the headset: master gain +12 alone (if it works,
done); otherwise boost +6 with pulse 2x, then adjust each to taste.

### fork.116: setup-time touches, write refusals, duplicate setup, and the settings queue

Seven fixes from an audit of fork.115, one of them in moonlight-common-c (pyrowave); none
changes what a working link sends.

- A finger on a pad during setup reached the host as a pointer it never saw go down. The state
  characteristic is subscribed first, so 250 Hz reports arrive before the controller is
  announced; the touch sent a DOWN to a listener with no context for the controller (dropped),
  and after the announcement the host got MOVEs (or nothing, after the dedupe) until the thumb
  lifted. `onReady` now forgets the pad state as it announces, without an UP (the host never
  saw the DOWN), so the next report sends a DOWN.
- An ATT refusal (status 1..0x7F) of a feature command, which the firmware answers with status
  4 for a message it does not know, was handled as a stack refusing a write request (the command
  characteristic offers write-without-response too): the link switched to write-without-response
  for good and re-queued the refused command, so every later settings write went without
  response and firmware refusals became invisible. The switch is now reserved for stack/link
  codes (0x80 and up, 133 among them); a peer refusal of a feature command drops that command
  and keeps the write type.
- The output-report framing ladder is gone. A refused id-less output report was retried with
  its report id, which fork.112 showed the firmware accepts and silently ignores, so one
  transient refusal silenced that report id for the rest of the link while `written` kept
  counting. A refusal is now retried once, id-less again and with a response, after the 50 ms
  write-retry delay; a second refusal within 2 s drops the report, and the framing stays id-less
  (never the id, never the feature forms, which the firmware refuses). The 10 s summary ends
  with each offered report's verdict for the link (`id-less framing: 0x80 accepted, 0x81
  refused x2, 0x82 not written`), so a silent link can be told from a refusing one.
- `onReady` ran again on a late or duplicate descriptor-write completion (`subscribeNext` finds
  nothing left): the keep-alive was re-armed, the reconnect budget reset, the setup commands
  re-queued and a second haptic self-test schedule interleaved with the first. Only the
  announcement was guarded; now the whole setup runs once per link.
- The host's haptic, settings, rumble and trigger-rumble packets were matched to a driver-fed
  controller by number alone. A fresh context is controller 0 until its first input assigns a
  number, so packets for a real controller 0 could be written to an unassigned Steam Controller.
  The USB-context loops now also require the number to be assigned (the input-device loops keep
  upstream's behaviour).
- The launch/resume and serverinfo failure lines (fork.115) appended the exception, and a 404 is
  a `FileNotFoundException` whose message is the whole URL, with the session key (`rikey`), its
  id and the unique id. The lines now name the exception class and the HTTP status only.
- common-c: a settings chunk was an ordinary event in the control callback queue, and more than
  32 haptic events queued behind it displaced it, so a burst of slide pulses while a 20-triple
  write waited dropped a chunk and the controller applied a partial write. Settings chunks now
  have their own bound (16) and are never displaced for a haptic event; a haptic burst evicts
  only haptic events. `tests/test_steam_settings.c` covers the burst over one chunk and over a
  chunked write, and the settings bound itself.

The harness grows from 171 to 183 checks: the DOWN after the announcement, the dropped feature
command, the id-less retry and drop with the summary verdicts, and the duplicate descriptor-write
completion.

### fork.115: launch logging, the feature-report command mask, and the self-test across a drop

Three fixes from an audit of fork.114, none of them changing what a working link sends.

- A launch that hangs was not visible in the stream log: this morning's log was silent for 23 s
  between `Surface is available, starting connection...` and the user giving up, because the
  launch and resume requests run without a read timeout and nothing was logged until they
  returned. The start path now logs the serverinfo request and the launch or resume request
  when it is sent (host address, app name and id) and again when it returns or fails, with the
  elapsed milliseconds. The requests themselves are unchanged, still with no read timeout.
- `triggerHaptic`, which rewrites a host's `0x82` command into the `ID_TRIGGER_HAPTIC_CMD`
  feature message when the output characteristic is missing, still assumed side 1 = left,
  which fork.114 corrected elsewhere. It now takes the pad from `isLeftPad()`, the measured mapping
  from fork.114: side 0 is the left pad (mask `0x01`), side 1 the right pad (`0x02`), anything
  else both. The mask's own bit assignment (bit 0 = left) is the existing assumption and has
  not been measured. This path is only taken when the output characteristic is missing or
  refuses the write.
- The haptic self-test's reports survived a link loss: `onLinkLost` removed every other
  callback but left the pending self-test runnables on the handler, and the next link's
  `onReady` scheduled a fresh set, so a drop and reconnect during the test played two
  interleaved schedules. Each report now carries the link it was scheduled for and does
  nothing if a different link is up when it runs, and `onLinkLost` removes the pending ones.
  `stop()` is unchanged.

The harness covers the mask for both sides and the schedule across a simulated drop and
reconnect: the lost link's reports write nothing to the replacement, which gets its own 18.

### fork.114: the side byte differs between the pulse and the command report

Measured on the author's controller on 2026-10-15, over USB (HID output reports through the
vendor collection) and over Bluetooth (writes to the same Valve-service characteristics this
client uses, captured with USBPcap on the PC's Bluetooth radio): a `0x81` pulse with side 0
lands on the right pad and side 1 on the left, as the client assumed, but a `0x82` command with
side 0 lands on the **left** pad and side 1 on the **right**. Every level was felt on both
transports, including the -14 dB click Steam sends in Big Picture, so neither the firmware's
BLE path nor these characteristics are where the stream's weak slide haptics come from.

Re-read with the right mapping, stream_24 shows the right pad received Steam's tick commands
(`82 01 01 00`) at under 2 per second, where Steam on a direct connection with the right pad as
a mouse sends 15 to 60 ticks per second on the same controller (both transports); the left pad
received the -14 dB clicks. What differs is how many commands Steam generates, which depends on
the active layout and on the pad input reaching it, and the per-pad counts below are what to
compare against those direct rates on the next stream run.

- The 10 s summary's per-pad counts (`host 0x82 x55 (L 37 R 18)`) now use the right numbering
  for each report; before fork.114 the 0x82 counts were labelled the wrong way round.
- The self-test addresses the right pad first for the commands as well as the pulses
  (`82 01 ..` then `82 00 ..`; `81 00 ..` then `81 01 ..`); fork.113 played its clicks and ticks
  on the opposite pad from its pulses.

**Resolved (stream_26, fork.114, boost 0).** On the host's desktop, with the right pad as a
mouse, Steam sends the streamed controller the same dense tick texture it sends a directly
connected one (`82 01 01 f7` at about 48 per second: `0x82 x475 (L 38 R 437)` then
`x567 (L 85 R 482)` per 10 s, all written, none evicted), and it feels as expected. The haptic
pipeline is at parity end to end, and the boost, pulse-scale and master-gain preferences are
no longer needed for slides (they stay, default off).

**Do not judge slide haptics on Big Picture's controller test page.** Every earlier "no slide
haptics" run (stream_23, 24, 25) was taken there. Steam puts a wired, and therefore also the
virtual, controller into a raw test mode on that page and sends no slide haptics for it (a
cable on the author's PC produced nothing for a right-pad slide there either). A controller
connected directly over Bluetooth keeps its desktop mouse ticks on that page only because one
of Steam's mode messages fails over Bluetooth (ATT error 4 in the capture), which made the
direct connection look like the reference when it was a side effect. Compare slide haptics on
the desktop or in a game layout with a mouse on the trackpad.

### fork.113: haptic self-test and per-side counts

fork.112 on the headset (stream_23, boost +6 dB, pulses 2x): "right pad has no haptic feedback
while scrolling most, if not all the time; left has it, but it feels incorrect". Across every
stream log so far Steam's tick commands (`82 <side> 01 ..`) have only ever gone to side 1 and its
click commands (`82 <side> 02 ..`) only to side 0, with the 400 µs pulses on both sides, side 1
dominant. The firmware numbers the sides 1 = left, 0 = right (hid-steam swaps its pad ids for
this report). The input path is not the cause: the client puts the left pad in the left half of
the single touchpad and the right pad in the right half, and the host maps the halves back the
same way (`vhf_gamepad.cpp`), which Steam's own controller test confirmed for both pads.

So the open question is whether the controller renders side 0 at all over BLE, and fork.113
makes that answerable without Steam in the loop:

- **Bluetooth haptic self-test at connect** (`checkbox_steam_controller_haptic_self_test`, off by
  default): 1.5 s after link setup the client writes, to the output characteristics exactly as a
  host report would go, right pad (side 0) then left (side 1): three click commands, then three
  tick commands, then three 2 ms pulses, 300 ms apart, the commands at the +6 dB ceiling. Boost
  and pulse scale do not apply. The log announces the schedule and the 10 s summary shows the
  writes (`written 0x82 x12`). What to note: which pad moves for each group. If the right pad
  stays silent for its own clicks and ticks, the firmware does not drive that pad from these
  reports over BLE (or the side numbering differs on this transport) and the next experiment is
  the side byte itself; if both pads respond, the firmware is fine and the difference is in what
  Steam sends for the right pad through the stream (rate or type), which the per-side counts
  below then show.
- The 10 s summary counts the host's 0x81 and 0x82 reports per side: `host 0x81 x23 (L 20 R 3),
  0x82 x52 (L 0 R 52)`. Scroll one pad at a time for a whole 10 s window and the line says
  whether Steam sent that pad anything.
- The 10 s stick/extents line adds `pad touch L yes/no R yes/no`, so a pad the controller never
  reported touched is visible too.

### fork.100: connection-attempt races and small MTUs

An audit of the driver (round 42) found three lifecycle gaps, none of them seen on the Galaxy
XR so far:

1. A connection result delivered on the stack's binder thread before `connectGatt()` returned
   was dropped (the handle was not published yet), and the dead handle was then stored as a
   live link: nothing armed a watchdog, no reconnect was scheduled, and the manager refuses to
   restart a driver whose link looks up, so the controller stayed silently unavailable for the
   stream. The driver now keeps such an early result and acts on it once `connectGatt()`
   returns (`the connection attempt failed before connectGatt returned` retires it; an early
   CONNECTED goes on to negotiate), and a 40 s connect watchdog recycles an attempt that never
   reports anything (`no connection result within 40000 ms; recycling the link`).
2. Two retirements of one link (a DISCONNECTED on the binder thread and a watchdog on the
   driver thread) could both run and spend two reconnect attempts. Retirement is now atomic:
   the second caller finds the handle already gone.
3. An MTU that stayed at 23 was only warned about; the link was announced to the host and
   every 45-byte state report then arrived truncated. The driver asks once more
   (`MTU 23 is too small for state reports; asking again`) and recycles a link that stays too
   small (`MTU 23 stayed too small for state reports; recycling the link`) instead of
   announcing a controller that never produces input.

If input still fails, include the app build, controller firmware, Android device/version and
logcat spanning stream start, several button presses, and stream stop. For example:
`adb logcat -d -v threadtime > moonlight-bluetooth.txt`.
