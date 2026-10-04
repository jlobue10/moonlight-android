# Stereo presentation on Android XR

## Problem

The AI SBS 3D render modes produce a side-by-side frame: left eye in the left half, right eye in
the right half (`utils/Stereo3DRenderer.java`). On a TV or a 3D monitor the display splits the
halves per eye. On an Android XR headset a normal app is a flat panel in Home Space, so the frame
is simply shown as two pictures next to each other and there is no stereopsis.

## Solution

Jetpack XR SceneCore can show a `SurfaceEntity` with `StereoMode.SIDE_BY_SIDE`: the runtime sends
the left half of every frame to the left eye and the right half to the right eye, on a quad
floating in Full Space. This fork renders its side-by-side output into that entity's `Surface`
instead of into the on-screen `GLSurfaceView` when:

- the device declares `android.software.xr.api.spatial` (API ≥ 34; the Galaxy XR does),
- a 3D render mode is active, and
- the setting *Stereo screen on Android XR* is on (default).

Everything else (phones, TVs, the setting off, SceneCore unavailable) keeps the flat view.

### Pieces

| File | Role |
|---|---|
| `xr/XrStereoPresenter.java` | All SceneCore use. Creates the `Session`, requests Full Space, waits for the `SPATIAL_3D_CONTENT` capability, creates the `SurfaceEntity` (quad sized to one eye's aspect, `setSurfacePixelDimensions` pinned to the frame size), optionally hides the main panel, follows the system's recommended pose, tears everything down and returns to Home Space. |
| `xr/SurfaceGlThread.java` | A GLSurfaceView-like EGL thread that drives the existing renderer against any `Surface` (ES 3 context, when-dirty or continuous rendering, queued GL events drained before the context is released). |
| `utils/Stereo3DRenderer.RenderHost` | The renderer's only dependency on its host (request a frame, continuous or not, run on the GL thread, size). `hostFor(GLSurfaceView)` adapts the flat path; `SurfaceGlThread` implements it for XR. |
| `ui/StreamContainer.java` | Chooses the path in `init()`: `startXrStereo()` or `createFlatStereoView()`. In XR mode the plain `SurfaceView` stays as the (black) app window so the activity keeps keyboard/mouse/gamepad focus. `onDestroy()` stops the GL thread and the presenter. |

Frame size: `2 × stream width` by `stream height` (full resolution per eye; the flat path draws
each eye into half the window instead). Quad: *Stereo screen width* setting (default 2.0 m) by
width / eye aspect, 2.0 m in front of the activity space origin until the system's
`SpatialModeChangeEvent` suggests a pose.

### Launching into Full Space

Requesting Full Space from inside the running stream turned out to stop and restart the Game
activity during the transition. Moonlight ends the stream and finishes its activity whenever
Android stops it, so a 3D stream was torn down before it started ("kicked back to the app list",
no dialog). `xr/XrLaunch.fullSpaceOptionsIfNeeded()` therefore gives `ServerHelper.doStart()` activity
options from `LaunchUtils.createBundleForFullSpaceLaunch()` when an XR device will stream in a 3D
render mode with the stereo screen enabled, so the activity is born in Full Space and the presenter
finds the 3D-content capability already there. As a belt-and-braces measure `Game.onStop()` ignores a
stop that arrives while the presenter is still waiting for Full Space.

### Settings (3D section)

- **Stereo screen on Android XR** (on): use the SurfaceEntity path on XR devices.
- **Stereo screen width** (2.0 m): physical quad width.
- **Hide the app window in stereo mode** (off): `MainPanelEntity.setEnabled(false)` while
  streaming. Off by default because the flat window carries the on-screen controls and the perf
  overlay, and because input focus behaviour with a hidden panel is unverified.

### Build

- `androidx.xr.scenecore:scenecore:1.0.0-rc01` and `androidx.xr.runtime:runtime:1.0.0-rc01`
  (Google Maven). They raise **minSdk to 24** (upstream Moonlight is 21) and pull in Kotlin stdlib,
  media3-exoplayer 1.10 and arcore transitively; the library ships its own R8 consumer rules.
- `Session.create(Activity)` is the non-suspend overload, so no coroutine bridge is needed from
  Java. `SessionExt.getScene(session)` is the Java spelling of `session.scene`.
- Manifest: `android.software.xr.api.spatial` declared `required="false"`.

## Known gaps and what to verify on the headset

1. **Does the entity appear and in stereo?** Start a 3D render mode; the app should jump to Full
   Space and show one floating screen with depth. Log line: `XR stereo: SIDE_BY_SIDE SurfaceEntity`.
   If it shows flat side-by-side instead, look for `XR stereo unavailable (...)` in the log.
2. **Input.** Gamepad, keyboard and mouse go through the flat app window; confirm it keeps focus in
   Full Space. If it does not, try with *Hide the app window* off (default) and move the window
   aside with the system handle.
3. **Surface size.** `setSurfacePixelDimensions` pins the buffer size; verify the overlay's stream
   details show the full per-eye resolution and that the frame is not letterboxed in the quad.
4. **Pacing.** `SurfaceGlThread` relies on `eglSwapBuffers` for pacing in continuous mode, as
   GLSurfaceView does. If the GPU runs hot while idle, add a frame-interval sleep there.
5. **Leaving the stream** must dispose the entity and return to Home Space (`onDestroy`). If the
   app stays in Full Space with a black quad, the presenter's `stop()` did not run.
6. The left/right eye assignment follows the renderer: the left half is drawn with negative
   parallax (`drawBothEyes`). If depth looks inverted, swap the halves in `drawBothEyes`, not in the
   entity's stereo mode.
