# Desktop touchpad mode

Tools for using a captured touchpad with a keyboard as a desktop input device, for example a
tablet with a keyboard cover streaming a Windows desktop. Everything here is **off by default**
and lives under **Input Settings**. With every option at its default the trackpad behaves
exactly as before.

Based on [ClassicOldSong/moonlight-android#608](https://github.com/ClassicOldSong/moonlight-android/pull/608)
by Lian-CrescentMoon (a draft developed on a Galaxy Tab S7+ with Samsung and third-party
keyboard covers), reworked so that each behaviour is opt-in.

## Settings

| Setting | Default | What it does |
| --- | --- | --- |
| **Desktop touchpad mode** | off | Master switch for the gestures, the keyboard shortcuts and the on-demand capture below. |
| Pause touchpad while typing | off | While on: touchpad touches that start within the chosen time after a (non-modifier) key press are dropped for their whole sequence, and a finger already on the pad is cancelled when typing starts. A mouse is never affected. Needs desktop touchpad mode. |
| Ignore touchpad edges | off | While on: touches that *start* within the chosen distance of the pad's edge are dropped (that is where palms land). A touch that starts inside and travels to the edge is unaffected. The distance uses the driver's resolution, or 4 % of the pad when none is reported, and never more than 15 % of the pad. Needs desktop touchpad mode. |
| Touchpad scroll speed | 100 % | Multiplier on two-finger scrolling (and its momentum) of a captured touchpad. |
| Touchpad pointer momentum | on | The previous behaviour: a fast one-finger flick keeps the pointer gliding. Switch it off for a mouse-like pointer. Two-finger scroll momentum is separate and unchanged. |
| Draw a cursor over the stream | off | Draws a small, half-transparent cursor that follows the finger with no delay while the pointer is captured, and sends the pointer position to the host as an absolute position so both cursors land on the same spot. Applies the host's measured acceleration curve. Not offered together with the system local cursor option. |
| Drawn cursor speed | 100 % | Extra factor on the drawn cursor on top of the host's acceleration; 100 % matches the host mouse. |

## Desktop touchpad mode

With the master switch on, a captured touchpad (`SOURCE_TOUCHPAD`) gains:

- **3- and 4-finger gestures** turned into the keyboard shortcuts Windows uses for the same
  gesture, since the stream protocol has no touchpad-contact input type:

  | Gesture | Sent to the host |
  | --- | --- |
  | 3 or 4 fingers up | Win+Tab (Task View) |
  | 3 or 4 fingers down | Win+D (show desktop) |
  | 3 fingers left/right | Alt held, Tab or Shift+Tab per step; Alt released when the fingers lift |
  | 4 fingers left/right | Ctrl+Win+Right / Left (switch virtual desktop, content follows the fingers) |
  | 3-finger tap | mouse back (X1) |
  | 4-finger tap | mouse forward (X2) |

  Thresholds scale with the pad (about 8 mm to trigger, 7 mm per switcher step, 3 mm tap slop
  when the driver reports a resolution). A third finger that lands more than 300 ms after the
  first is treated as a resting thumb, and no gesture starts within 350 ms of typing. The
  Samsung keyboard cover (`sec_touchpad`) reports the vertical direction inverted and is
  corrected. Holding three or more fingers still for three seconds toggles on-screen gesture
  diagnostics (toasts).
- **Pinch to zoom**: a two-finger pinch sends Ctrl + mouse wheel, one notch per 15 % change of
  finger distance, which is what Windows itself does for most apps. The first 2.5 mm of a
  two-finger movement are held back until it is clear whether the fingers move together
  (scroll, handed to the normal trackpad handling in one step) or apart (zoom). On Android 14+
  an uncaptured pointer's classified pinch is handled too.
- **Keyboard shortcuts** (consumed, never sent to the host):
  - `Ctrl+Shift+M` mutes the touchpad until pressed again (for pads with no off switch).
  - `Ctrl+Shift+I` toggles whether keyboard and touchpad go to the PC or stay on the device,
    like Parsec. All host modifiers are released first so a half-held Ctrl or Shift cannot turn
    later keys into Sunshine's Ctrl+Alt+Shift shortcuts.
  - ``Ctrl+Alt+` `` ends the session (Artemis only has Ctrl+Alt+Shift+Q).
- **On-demand capture** of touchpads that Android presents as a touchscreen or mouse-with-finger
  until captured (the Samsung cover again). Without it those pads never got two-finger
  scrolling and the tablet's own gestures kept winning. Capture is requested when such a pad is
  used, at most five times a second apart, so devices where capture is unavailable (DeX) give
  up quietly.
- On Samsung devices the screenshot key of the keyboard cover (`KEYCODE_SYSRQ`) is requested
  through `SemWindowManager.requestSystemKeyEvent` while input is grabbed, so it reaches the
  host as Print Screen. Whether a third-party app is allowed to do this is logged.

Focus loss and the end of the stream release any Alt or Ctrl a gesture was holding.

## Always-on fixes

Two small corrections from the same work apply regardless of the settings:

- Print Screen is sent as Windows `VK_SNAPSHOT` (0x2C). The previous constant (154) was the
  Java AWT value and the host ignored it.
- A double tap on the trackpad no longer turns into a drag on the first unit of jitter under a
  resting finger; only leaving the tap bounds starts the drag. The flag that made the following
  tap a double click again is cleared. When the app sends absolute positions (drawn cursor), the
  second click of a double tap is delayed 50 ms so that Sunshine's deferred LEFT release does
  not swallow it; in relative mode the clicks are sent immediately, as before.

## Not taken from the original PR

- Removing the keyboard accessibility service from the manifest (a packaging decision the author
  kept separate).
- The Gradle wrapper checksum (this fork is on a different Gradle version).
- The Korean settings text, replaced with English.
- Its defaults: a 1 s typing pause, a 3 mm edge margin and pointer momentum off applied to every
  user there; here they are opt-in.

## Tests

- `tools/regression/test_touchpad_gestures.py`: the production `TouchpadGate` and
  `TrackpadGestureDetector` with scripted contacts and keys (typing pause, edge rejection,
  toggle, every swipe, taps, late fingers, typing guard, pinch versus scroll, classified pinch,
  reset).
- `tools/regression/test_trackpad_lifecycle.py`: the existing trackpad checks plus the scroll
  speed factor, the pointer momentum setting, the move sink and the double-tap jitter tolerance.

Not verified here: a real tablet. The original author reported working gestures, scrolling,
drag, double taps and the drawn cursor on a Tab S7+, with pinch zoom built but not confirmed.
