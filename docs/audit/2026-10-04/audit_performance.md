# Performance audit — jlobue10/moonlight-android (Artemis fork, branch `moonlight-noir`, versionName 20.2.6)

Repository audited read-only at `/home/user/jlobue10/moonlight-android` (HEAD `c5cf27f4`, identical to ClassicOldSong/moonlight-android). Comparison baseline: `og/master` = moonlight-stream/moonlight-android v12.2 (`b48494cb`), merge-base `27ded2ad`. All line numbers below refer to the fork unless prefixed `og:`. "CONFIRMED" = established by reading the code; "SUSPECTED" = magnitude or user-visible effect needs on-device profiling.

## A. Executive summary

The native/JNI/audio/input plumbing is essentially upstream Moonlight and is in good shape. The regressions are concentrated in three places the fork rewrote: the video **render loop** (`MediaCodecDecoderRenderer.startRendererThread`), the **frame-rate/mode plumbing** in `Game.onCreate`, and the **manifest/3D/common-c integration**. Several user-facing options (Frame Pacing modes) are dead code because of the rewrite.

Top 5 wins (highest expected latency/frame-pacing/battery impact per line changed):

1. **Restore the frame-pacing preference and upstream's render loop.** `Game.java:690-705` overwrites `prefConfig.framePacing` with `FRAME_PACING_BALANCED` for everyone, so "Minimum latency", "Cap FPS" and "Max smoothness" in the settings UI do nothing, and the renderer's added "latest-only" poll (`MediaCodecDecoderRenderer.java:1211-1247`) plus 2 ms/0.5 ms dequeue timeouts (`:62-63, :83, :1252-1258`) turn the render thread into a ~1,000+ wakeups/s poller that also races the Choreographer path and can present frames out of order. Reverting to `og:` `dequeueOutputBuffer(info, 50000)` + honoring `framePacing` is the single largest fix.
2. **Delete `applySurfaceFrameRate()`** (`MediaCodecDecoderRenderer.java:611, 2412-2424`). It runs after `Game.surfaceCreated()` and overrides the carefully chosen `setFrameRate(desiredRefreshRate, FIXED_SOURCE, CHANGE_FRAME_RATE_ALWAYS)` with `setFrameRate(streamFps, DEFAULT)` (ONLY_IF_SEAMLESS), which on 90/120 Hz panels asks the OS to *drop* the panel to the stream rate — the opposite of upstream's intent. Then port upstream `6d4c64a5` (`setProducerThrottlingEnabled(false)`).
3. **Restore `android:appCategory="game"` / `android:isGame="true"`** in `AndroidManifest.xml` (both present in `og:`; removed in the fork). Without them `GameManager.setGameState()` is a documented no-op for non-game packages, `game_mode_config` is never consulted, and OEM game-performance modes keyed on app category don't engage.
4. **Stop loading OpenCV on every stream start in 2D mode.** `StreamContainer.init()` line 74 writes `Stereo3DRenderer.isMovieMode`, which runs `Stereo3DRenderer`'s static initializer → `OpenCVLoader.initLocal()` → `dlopen` of `libopencv_java4.so` (23.5 MB arm64 / 15.6 MB armv7, measured from the 4.12.0 AAR) on the main thread inside `Game.onCreate`, even when 3D is off.
5. **Rebase `moonlight-common-c` onto upstream and apply `68adf9ec` + `abde6021`.** The fork's common-c (`c999436`) still uses the scalar Reed-Solomon (`reedsolomon/rs.c`) that upstream replaced with SIMD `nanors` (`de364b6`, `5551d29`, `1f76427`), lacks enqueue-side input batching (`e59a5f5`) and the lock-free RTT query (`20c05ed`). `68adf9ec` removes the H.264 level_idc/constraint rewriting on Oreo+ that the fork still performs; `abde6021` moves controller LED binder calls off the control-stream callback thread.

## B. Findings (ordered by expected impact)

### HIGH

#### H1. Frame Pacing setting is dead; everyone runs a rewritten "Balanced" path
- **Where:** `Game.java:690-705`, `Game.java:757-773`, `MediaCodecDecoderRenderer.java:1284-1429`, `PreferenceConfiguration.java:640-669`, `res/values/arrays.xml:122-137`, `res/xml/preferences.xml:55-59`.
- **Code:**
  ```java
  // Game.java:691-705
  if (prefConfig != null && prefConfig.preferLowerDelays) {
      decoderRenderer.setPreferLowerDelays(true);
      decoderRenderer.setPreferLowerDelaysTimeoutUs(500);  // 0.5 ms
      prefConfig.framePacing = PreferenceConfiguration.FRAME_PACING_BALANCED;
  } else {
      decoderRenderer.setPreferLowerDelays(false);
      decoderRenderer.setPreferLowerDelaysTimeoutUs(2000); // 2 ms
      prefConfig.framePacing = PreferenceConfiguration.FRAME_PACING_BALANCED;
  }
  ```
- **Mechanism (CONFIRMED):** `framePacing` is overwritten before the CAP_FPS block at `:757` (now unreachable) and before the renderer reads it, so the `latency`, `cap-fps` and `smoothness` entries in the Frame Pacing list (`arrays.xml:130-137`) do nothing. The whole non-balanced branch of the render loop (`:1284-1429`, the EWMA/drop-threshold code) is dead. Every user gets the Choreographer path, which by design holds up to 2 decoded frames (`OUTPUT_BUFFER_QUEUE_LIMIT = 2`, comment at `:1086-1088`: "1 extra frame of buffer to smooth over network/rendering jitter") and releases at most one frame per vsync (`doFrame`, `:1083`). Users who select "Minimum latency" still pay that extra frame. `getSelectedFramePacingName()` (used by the perf log) still reports the user's choice, so the perf log misattributes results.
- **Upstream:** `og:MediaCodecDecoderRenderer.java:1031-1055` honors `prefs.framePacing`: min-latency renders immediately with `releaseOutputBuffer(lastIndex, System.nanoTime())`, smoothness/cap-fps with `releaseOutputBuffer(lastIndex, 0)`, balanced via Choreographer. `og:Game.java` never rewrites `framePacing`.
- **Recommendation:** Remove the two `prefConfig.framePacing = FRAME_PACING_BALANCED` assignments. Map the "pref_low_latency_frame_balance" toggle (`PreferenceConfiguration.java:635-639`, default `false`) onto `FRAME_PACING_MIN_LATENCY` rather than a private timeout knob. Delete the dead `:1284-1429` branch (or restore upstream's 25 lines).

#### H2. Render thread busy-polls MediaCodec (~1,000+ dequeue round-trips/s) and races the Choreographer path
- **Where:** `MediaCodecDecoderRenderer.java:60-63, 83, 1161, 1211-1260, 1430-1451`, `:1089-1097` (`doFrame`).
- **Code:**
  ```java
  // :83
  private int getOutputDequeueTimeoutUs(){ return preferLowerDelays ? Math.max(250, preferLowerDelaysTimeoutUs) : preferLowerDelaysTimeoutUs; }
  // :1212-1247 (runs when preferLowerDelays == false, i.e. the default)
  if (!preferLowerDelays) {
      android.media.MediaCodec.BufferInfo __tmpInfo = new android.media.MediaCodec.BufferInfo();
      int __idx = videoDecoder.dequeueOutputBuffer(__tmpInfo, 0);
      ... // drain non-blocking, keep newest
      if (__last >= 0) { releaseWithPolicy(__last, System.nanoTime()); ...; continue; }
  }
  // :1252-1258
  int outIndex = videoDecoder.dequeueOutputBuffer(info, getOutputDequeueTimeoutUs());   // 2000 us (500 us with preferLowerDelays)
  if (outIndex == MediaCodec.INFO_TRY_AGAIN_LATER) {
      tryAgainStreak++;
      int backoffUs = (tryAgainStreak <= 2) ? 250 : 500;
      outIndex = videoDecoder.dequeueOutputBuffer(info, backoffUs);
  }
  ```
- **Mechanism (CONFIRMED):**
  1. *CPU/battery:* each idle iteration is a non-blocking dequeue + a ≤2 ms blocking dequeue + a 250/500 µs blocking dequeue, i.e. ~400 iterations/s and ~1,200 `dequeueOutputBuffer` calls/s (each is a JNI call that posts to the MediaCodec looper and waits for a reply — a cross-thread round trip), with `preferLowerDelays` ~2,400/s. This runs for the whole session at `THREAD_PRIORITY_URGENT_DISPLAY` (`:1161`) and prevents the core from reaching deep idle states between frames. Upstream blocks for 50 ms (`og:1023`), ≤20 calls/s; the codec wakes the waiter the moment output is ready, so polling buys no latency.
  2. *Ordering race:* when the ≤2 ms blocking dequeue returns frame A it goes into `outputBufferQueue` for the Choreographer (`:1449`); if frame B completes in the few microseconds before the next iteration's non-blocking poll (typical after a jitter burst, when decoders release consecutive outputs back-to-back), B is presented *immediately* with timestamp `now` (`releaseWithPolicy`, `:67-82`) while A is presented later by `doFrame` with a later timestamp → newer frame displayed before older (visible back-step), and the one-frame-per-vsync gate is bypassed.
  3. *Stats:* frames rendered through the latest-only block never increment `totalFramesRendered`, so the perf overlay's "Rendering FPS" undercounts.
  4. *GC:* `new BufferInfo()` is allocated every loop iteration (`:1215`), ~400-1,000 allocations/s, plus the `Long` boxing described in M3.
- **Upstream:** `og:MediaCodecDecoderRenderer.java:1019-1023` — one `BufferInfo` for the thread's life, `dequeueOutputBuffer(info, 50000)`, inner drain with timeout 0 (`og:1033`).
- **Recommendation:** Delete the `LATEST_ONLY_LOW_LATENCY` block, `releaseWithPolicy`, `preferLowerDelaysTimeoutUs` and the backoff; restore the 50 ms blocking dequeue. Keep the raised thread priority if desired (it is harmless once the thread actually blocks). Magnitude of the CPU saving is SUSPECTED (measure with Perfetto: "Video - Renderer (MediaCodec)" wakeups and CPU idle residency during a static scene).

#### H3. `applySurfaceFrameRate()` overrides the display frame-rate hint chosen in `Game`
- **Where:** `MediaCodecDecoderRenderer.java:611` (`configureAndStartDecoder`), `:2412-2424`; `Game.java:3811-3844` (`surfaceCreated`), `Game.java:1627-1628`.
- **Code:**
  ```java
  // MediaCodecDecoderRenderer.java:2416-2418
  if (android.os.Build.VERSION.SDK_INT >= 30) {
      surface.setFrameRate((float) targetFps, android.view.Surface.FRAME_RATE_COMPATIBILITY_DEFAULT);
  // Game.java:3832-3838 (runs earlier, on surface creation)
  holder.getSurface().setFrameRate(desiredFrameRate, Surface.FRAME_RATE_COMPATIBILITY_FIXED_SOURCE, Surface.CHANGE_FRAME_RATE_ALWAYS);
  ```
- **Mechanism (CONFIRMED order, SUSPECTED per-device effect):** `surfaceCreated` fires when the SurfaceView surface exists; the connection starts only after `surfaceChanged` (`StreamContainer.java:240-247` → `Game.java:867-879`), and the decoder is configured later still from the RTSP thread, so the decoder's call is always the last `setFrameRate` on the surface and wins. `Game` deliberately requests `desiredRefreshRate` (e.g. 120 for a 60 fps stream on a 120 Hz panel when "reduce refresh rate" is off; comment `:3825-3828` "ensures the lowest possible display latency") with FIXED_SOURCE and CHANGE_FRAME_RATE_ALWAYS. The decoder then re-requests the *stream* fps with `FRAME_RATE_COMPATIBILITY_DEFAULT` and the two-arg overload's implicit `CHANGE_FRAME_RATE_ONLY_IF_SEAMLESS`, inviting the OS to lower the panel to 60 Hz (seamless on most LTPO panels) → up to one extra 8.3 ms scan-out period of latency and a different outcome per device. It is re-applied on every codec recovery.
- **Upstream:** only `og:Game.java:2547-2555` (surfaceCreated) sets the frame rate, then `og:2559-2561` disables producer throttling.
- **Recommendation:** Delete `applySurfaceFrameRate()` and its call at `:611`. Also delete the reflective `SurfaceView.setFrameRate(float,int)` lookup in `Game.java:928-931` — no such method exists on `SurfaceView`/`View`, so it is a swallowed `NoSuchMethodException` (dead).

#### H4. Missing upstream `6d4c64a5` — producer throttling left enabled on the video surface
- **Upstream change:** `og:Game.java:2558-2561`:
  ```java
  // Disable producer throttling on the underlying surface for reduced latency
  if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.CINNAMON_BUN) {
      holder.getSurface().setProducerThrottlingEnabled(false);
  }
  ```
- **What it does / applicability:** On Android 17 (API 37, `CINNAMON_BUN`) the platform can throttle a BufferQueue producer (here MediaCodec) to the display's cadence; disabling it lets the decoder dequeue/queue output buffers as soon as frames are decoded so the newest frame is available at the next composition (per the upstream commit message). It is a one-line, version-gated addition in `surfaceCreated`. The fork targets `compileSdk 36`, so the constant/method do not exist at compile time; porting requires `compileSdk 37` (upstream uses 37) or an `SDK_INT >= 37` check with reflection. CONFIRMED missing; benefit SUSPECTED (only Android 17+ devices).

#### H5. Manifest no longer declares the app as a game (Game Mode / GameManager hooks are no-ops)
- **Where:** `AndroidManifest.xml:46-59` (`<application>`), `:79-81` (`android.game_mode_config` meta-data), `UiHelper.java:41-53`.
- **Diff vs upstream:** `og:` has `android:isGame="true"` and `android:appCategory="game"` on `<application>`; the fork removed both (`git diff og/master -- app/src/main/AndroidManifest.xml`).
- **Mechanism (CONFIRMED by diff; runtime effect is platform-documented):** `GameManagerService.setGameState()` is a no-op for packages not categorized as games, so `UiHelper.notifyStreamConnected/EnteringPiP/Ended` (called from `Game.java:1245, 1282, 3462, 3706`) do nothing; the `game_mode_config` XML (which disables downscaling/FPS override) is never read; OEM game boosters and the platform's game-oriented scheduling/thermal policies that key on `appCategory="game"` do not engage; Game Dashboard is unavailable. SUSPECTED magnitude (device-specific), but the code the fork kept (`UiHelper`, the meta-data) is inert without the attributes.
- **Recommendation:** Restore `android:isGame="true"` and `android:appCategory="game"`.

#### H6. OpenCV (23.5 MB `.so`) is loaded on the main thread at every stream start, even in 2D
- **Where:** `StreamContainer.java:74`, `Stereo3DRenderer.java:64, 136-142`.
- **Code:**
  ```java
  // StreamContainer.init(), :74 — executed for every Game.onCreate
  Stereo3DRenderer.isMovieMode = renderMode == StreamMode.MODE_AI_3D_MOVIE;
  // Stereo3DRenderer.java:136-142
  static {
      if (!OpenCVLoader.initLocal()) { LimeLog.severe(...); } else { LimeLog.info("OpenCV library found inside package. Using it!"); }
  }
  ```
- **Mechanism (CONFIRMED):** `isMovieMode` is a non-final static, so the write triggers class initialization and the static block, which calls `System.loadLibrary("opencv_java4")` (`org.opencv:opencv:4.12.0` ships `jni/arm64-v8a/libopencv_java4.so` = 23,465,088 bytes, armeabi-v7a = 15,622,192 bytes, plus 1.3 MB `libc++_shared.so`; measured from the AAR). This happens synchronously in `Game.onCreate` before the connection starts: relocation processing of a 23 MB library plus its `JNI_OnLoad`, and the library stays mapped for the rest of the process. The 3D renderer itself is otherwise fully bypassed in 2D (`StreamContainer.init` only creates the `GLSurfaceView`/`Stereo3DRenderer` when `renderMode != MODE_2D`, `:86-94`; LiteRT and the 17.7 MB `.tflite` are only touched in `initializeTfLite()` from `onSurfaceCreated`).
- **Recommendation:** Move `isMovieMode` into `StreamContainer` (pass it to the renderer constructor) and load OpenCV lazily inside `Stereo3DRenderer`'s constructor/`onSurfaceCreated`. Cost in ms is SUSPECTED (measure `Game.onCreate` with Perfetto); the APK/RSS cost is confirmed.

### MEDIUM

#### M1. Missing upstream `68adf9ec` — H.264 level_idc and constraint-flag rewriting still done on Oreo+
- **Where:** `MediaCodecDecoderRenderer.java:1726-1741` (`doProfileSpecificSpsPatching`), `:1917-1936` (level_idc).
- **Code:**
  ```java
  // :1736-1740
  else {
      // Force the constraints unset otherwise (some may be set by default)
      sps.constraintSet4Flag = false;
      sps.constraintSet5Flag = false;
  }
  // :1917-1931
  if (!refFrameInvalidationActive) {
      if (initialWidth <= 720 && initialHeight <= 480 && refreshRate <= 60) { sps.levelIdc = 31; }
      else if (initialWidth <= 1280 && initialHeight <= 720 && refreshRate <= 60) { sps.levelIdc = 32; }
      else if (initialWidth <= 1920 && initialHeight <= 1080 && refreshRate <= 60) { sps.levelIdc = 42; }
  ```
- **What upstream changed:** `og:1337` → `else if (Build.VERSION.SDK_INT < Build.VERSION_CODES.O)` and `og:1441` → `if (!refFrameInvalidationActive && Build.VERSION.SDK_INT < Build.VERSION_CODES.O)`. On Oreo+ the code already always writes `bitstream_restriction`/`max_dec_frame_buffering = num_ref_frames` (`:1966-1992`), which is the modern way to tell the decoder it needs only one DPB slot; the legacy level/constraint rewrite was for pre-Oreo OMX decoders. On modern decoders the rewrite can only hurt: lowering `level_idc` to 4.2 for a 1080p60 stream mis-declares the stream when the bitrate exceeds that level's limit (Codec2/vendor decoders validate level), and clearing `constraint_set4/5` discards the encoder's "constrained high / no B-frames" declaration that lets decoders choose their low-delay path. The fork never patches `constraintSet4/5` *on* except the Intel-only `constrainedHighProfile` list, so the live effect is the two `= false` lines and the level change.
- **Applies to the fork?** Yes, directly — the two hunks apply cleanly to `:1736` and `:1917`. Which H.264 path is used (vs HEVC/AV1) depends on the device; the change is version-gated so it is safe. Benefit SUSPECTED per decoder; risk removal CONFIRMED by reading.

#### M2. Decode-latency bookkeeping: unsynchronized `LongSparseArray<Long>` with unbounded growth
- **Where:** `MediaCodecDecoderRenderer.java:58, 86-98, 1688, 1223, 1441`.
- **Code:**
  ```java
  private final LongSparseArray<Long> enqueueNsByPtsUs = new LongSparseArray<>();      // :58
  try { enqueueNsByPtsUs.put(timestampUs, System.nanoTime()); } catch (Throwable ignored) {}   // :1688, input thread
  Long enqNs = enqueueNsByPtsUs.get(presentationTimeUs); ... enqueueNsByPtsUs.delete(...)   // :87-89, render thread
  ```
- **Mechanism (CONFIRMED):** `put` runs on the thread calling `submitDecodeUnit` (the common-c receive/decoder thread), `get/delete` on the render thread; `LongSparseArray` is not thread-safe (binary-search over arrays that are reallocated on growth) — torn reads return wrong values or throw `ArrayIndexOutOfBounds`; the call at `:1456` is inside a `catch (IllegalStateException)` only, so such an exception kills the render thread (stream freezes). Entries for frames released without rendering (`:1223` latest-only drain, `:1441` queue overflow) are never deleted, so the map (and its boxed `Long`s) grows for the whole session proportionally to dropped frames. It also boxes a `Long` per frame.
- **Upstream:** `og:1079-1086` computes `delta = uptimeMillis() - presentationTimeUs/1000` — no map, no allocation (the PTS *is* the enqueue time: `timestampUs = enqueueTimeMs * 1000`, `:2149`). The ns precision bought here is below the ms granularity of the stat anyway.
- **Recommendation:** Revert to upstream's computation; delete the map.

#### M3. Scalar Reed-Solomon FEC (fork common-c) vs upstream SIMD `nanors`
- **Where:** `app/src/main/jni/moonlight-core/Android.mk:31` (`moonlight-common-c/reedsolomon/rs.c`), submodule `moonlight-common-c @ c999436` (`reedsolomon/rs.c:96-106`, `src/RtpVideoQueue.c:267`).
- **Code:**
  ```c
  // reedsolomon/rs.c:96-105 — one table lookup + XOR per byte
  static void addmul(gf *dst1, gf *src1, gf c, int sz) {
      USE_GF_MULC;
      if (c != 0) { ... for (; dst < lim; dst++, src++) GF_ADDMULC(*dst, *src); }
  }
  ```
- **Mechanism (CONFIRMED divergence, SUSPECTED magnitude):** FEC decode runs on the video receive thread whenever any packet of a block is missing; cost ∝ (bitrate × parity ratio). Upstream common-c replaced this with `nanors` (`de364b6 Use nanors for optimized Reed-Solomon FEC decoding`, `5551d29 Use SIMDe for NEON acceleration`, `1f76427 Switch to upstream nanors with native SIMD and GFNI runtime dispatching`; `og` tree has `nanors/` and no `reedsolomon/`). At 50-150 Mbps on lossy Wi-Fi the scalar path can add milliseconds per recovered frame on little cores — exactly when the connection is already struggling. Same submodule bump also brings `e59a5f5 Rewrite gamepad input batching to batch on the enqueue-side`, `b126e48 Improve locking for batched mouse and gamepad sensor events`, `20c05ed Don't lock the ENet mutex when querying for RTT information` (the fork's `LiGetEstimatedRttInfo`, `ControlStream.c:1694-1697`, takes `enetMutex`; the perf overlay calls it once per second from the receive thread via `MoonBridge.getEstimatedRttInfo()`, `MediaCodecDecoderRenderer.java:1792`), and `e356b2c` µs timestamps.
- **Recommendation:** Merge upstream common-c into the ClassicOldSong fork (its only extra is `c999436 Add send empty payload method`), update `Android.mk` to the `nanors/deps/obl/*.c` + `nanors/rs.c` sources as in `og:Android.mk:31-33`.

#### M4. Missing upstream `abde6021` — controller LED binder calls on the control-stream thread, bogus RGB capability pre-Android 14
- **Where:** `ControllerHandler.java:788-795` (`hasRgbLed`), `:2426-2459` (`handleSetControllerLED`), `:3234-3238` (`destroy`).
- **Code:**
  ```java
  // :2438-2455 — runs on moonlight-common-c's control-stream callback thread (bridgeClSetControllerLED → Game.setControllerLED)
  if (deviceContext.lightsSession == null) {
      deviceContext.lightsSession = deviceContext.inputDevice.getLightsManager().openSession();
  }
  ... for (Light light : deviceContext.inputDevice.getLightsManager().getLights()) { if (light.hasRgbControl()) ... }
  deviceContext.lightsSession.requestLights(lightsRequestBuilder.build());
  ```
- **Mechanism (CONFIRMED divergence):** `openSession()`/`getLights()`/`requestLights()` are synchronous binder calls into system_server executed on the thread that processes control-stream packets (rumble, LED, HDR, status), so a slow `LightsManager` stalls rumble and status handling. Because `Light.hasRgbControl()` returned true for every light before Android 14 (upstream comment), `hasRgbLed` is true for any controller with a player LED, `LI_CCAP_RGB_LED` is advertised, the host sends LED packets for every such controller, and each one triggers these calls. `lightsSession.close()` in `destroy()` runs on the main thread from `Game.onPause/onStop` → `controllerHandler.stop()` without the try/catch upstream added. Upstream posts a coalesced `setLedStateRunnable` to the existing `backgroundHandlerThread`, forces `hasRgbLed = false` pre-UDC for non-Sony VIDs, and guards `close()`.
- **Recommendation:** Apply `abde6021` as-is (it applies to this file; the fork's surrounding code at `:2426-2459` is identical to upstream's pre-commit version).

#### M5. "Prevent packet loss" option: 50 reliable control packets/s from the UI thread
- **Where:** `Game.java:333-338`, `:3716-3718`; `moonlight-common-c/src/ControlStream.c:2067-2077`.
- **Code:**
  ```java
  private final Runnable backgroundPing = () -> {
      if (connected) { timerHandler.postDelayed(Game.this.backgroundPing, 20); MoonBridge.sendEmptyPayload(); }
  };
  ```
  ```c
  int LiSendEmptyPayload() {
      uint8_t payload[4] = {0xAA, 0x55, 0xAA, 0x55};
      return sendMessageAndForget(0x00, sizeof(payload), payload, CTRL_CHANNEL_SERVERCTL, ENET_PACKET_FLAG_RELIABLE, false);
  }
  ```
- **Mechanism (CONFIRMED):** when `checkbox_prevent_packet_loss` is on, the main looper wakes every 20 ms for the whole session and each tick sends an ENet **reliable** packet (requiring an ACK from the host): ~100 extra packets/s and retransmission bookkeeping, on top of common-c's own periodic pings. Opt-in, but the name suggests a free lunch. If the goal is to keep the Wi-Fi radio out of power save, the `WIFI_MODE_FULL_LOW_LATENCY` lock the app already holds (`Game.java:553-557`) is the sanctioned mechanism.
- **Recommendation:** Use `ENET_PACKET_FLAG_UNSEQUENCED` (unreliable) and drive it from common-c's control-stream timer thread rather than the UI `Handler`; consider 100 ms.

#### M6. Stereo 3D path: synchronous `glReadPixels`, busy-wait on inference, continuous rendering, per-frame logging (only when 3D is enabled)
- **Where:** `Stereo3DRenderer.java:414-524` (`onDrawFrame`), `:647-658` (`readPixelsForAI`), `:660-687` (`readPixelsForAI_Async`, unused), `:862-937`, `:455, :479, :1037, :1143`; `StreamContainer.java:82-94`.
- **Code:**
  ```java
  // :653 — synchronous readback on the GL thread every frame
  GLES20.glReadPixels(0, 0, modelInputWidth, modelInputHeight, GLES20.GL_RGBA, GLES20.GL_UNSIGNED_BYTE, destinationBuffer);
  // :464-470 — movie mode blocks the GL thread until inference finishes
  while ((newMap = latestDepthMap.getAndSet(null)) == null) { try { Thread.sleep(1); } catch (InterruptedException e) {} }
  // :419-423 — non-movie mode renders continuously at display rate even without a new video frame
  if (!isMovieMode) { glSurfaceView.setRenderMode(GLSurfaceView.RENDERMODE_CONTINUOUSLY); }
  ```
- **Mechanism (CONFIRMED):** per frame on the GL thread: `updateTexImage`, a 256×256 downscale draw, a synchronous `glReadPixels` (CPU waits for the GPU → pipeline bubble each frame; the PBO double-buffered variant that would avoid it exists at `:660-687` but is never called), a sampled diff, upload of the depth map, two blur passes and two eye draws. In movie mode the thread additionally spins in a 1 ms sleep loop until the AI thread produces a depth map, capping display updates at inference rate; in non-movie mode `RENDERMODE_CONTINUOUSLY` keeps the GPU drawing at panel refresh regardless of new frames (battery). `AiResultHandling` runs Sobel + four histograms + `compareHist` (`:862-937`) per inference result in addition to the TFLite run. `Log.d` with string concatenation runs 3-4× per frame across three threads in release builds (`:455, :479, :1037, :1143`; `Log.d` is not stripped). `StreamContainer.init()` also leaves an unused plain `SurfaceView` child beneath the `GLSurfaceView` (`:83-84, :87-93`), and `Game.java:894-902` then applies `setFixedSize` to that dummy view. Latency: the video goes through a `SurfaceTexture` → GL → `GLSurfaceView` hop instead of straight to the compositor (inherent to the feature).
- **Recommendation:** Switch to `readPixelsForAI_Async`; replace the sleep loop with a `BlockingQueue.poll(timeout)` or render the previous depth map; gate `Log.d` behind `isDebugMode`; remove the dummy `SurfaceView` or give it `setZOrderMediaOverlay`-free `GONE`.

### LOW

#### L1. Release build disables R8 optimization (`proguard-android.txt`) and keeps whole third-party packages
- **Where:** `app/build.gradle:139` (`proguardFiles getDefaultProguardFile('proguard-android.txt')`), `app/proguard-rules.pro:1-2, 15-17, 44-46`.
- **Mechanism (CONFIRMED via gradle diff):** upstream uses `proguard-android-optimize.txt`; AGP's plain `proguard-android.txt` carries `-dontoptimize`, which turns off R8 inlining/class merging/outlining even though `minifyEnabled true`; both repos also use `-dontobfuscate`. The fork additionally `-keep`s `org.opencv.**`, `org.tensorflow.lite.gpu.**` and `com.github.mikephil.charting.**` wholesale. Effect: larger DEX and class-loading work at cold start; no effect on the streaming hot loops once JIT/AOT compiled (SUSPECTED magnitude).
- **Recommendation:** Use `proguard-android-optimize.txt`; narrow the keeps to the JNI-registered classes (OpenCV's `org.opencv.core.Mat`/`Core`/`Imgproc` natives need keeping; the rest does not).

#### L2. Perf-overlay stats formatting and `TrafficStats` binder calls on the video receive thread
- **Where:** `MediaCodecDecoderRenderer.java:1773-1893`, specifically `:1795-1796, :1855-1856`.
- **Mechanism (CONFIRMED):** `submitDecodeUnit` runs on common-c's receive thread for every `CAPABILITY_DIRECT_SUBMIT` decoder (`c2.*` and the OMX list, `MediaCodecHelper.java:59-74`). Once a second it builds the overlay string with ~10 `context.getString(...)`/`String.format` calls and, when the overlay is on, performs three `TrafficStats.getUidRx/TxBytes` calls — binder IPCs into `NetworkStatsService` — on the thread that must drain UDP. Upstream formats on the same thread but does no IPC. Low impact (3 IPC/s), but it is the one thread that should never block; post the raw counters to a `Handler` instead.

#### L3. Dead/duplicate code in the decoder path
- `MediaCodecDecoderRenderer.java:1479-1494` "C2 sleep watchdog" sits *outside* the `while (!stopping)` loop, so it runs once at shutdown (calling `flush()`/`setParameters` on a stopping codec) and never during streaming (CONFIRMED). `MediaCodecHelper.applyExtraVendorOptions()` (`:1161-1193`) and `isMTKDecoderName()` (`:2428-2432`) are never called. `Game.java:673-688` reads `forceTightThresholds` by reflection but no preference ever sets it (`PreferenceConfiguration.java:235` is a constant `false`). The `if (SDK_INT >= 21) ... else if (SDK_INT >= 21)` ladders at `:1102-1109, :1338-1346, :1412-1420` are no-ops.

#### L4. `setFixedSize` + reflection hack in `Game.onCreate`
- **Where:** `Game.java:890-934`.
- **Mechanism (CONFIRMED):** `streamSurfaceView.getHolder().setFixedSize(prefConfig.width, prefConfig.height)` uses the un-inverted resolution (wrong in portrait with `autoInvertVideoResolution`), contradicts the retained upstream comment at `:1618` ("Don't do setFixedSize since it might not update the view dimensions correctly when entering PiP mode"), and is moot for a MediaCodec-driven surface (the codec sets its own buffer dimensions). `setZOrderOnTop(false)`/`setZOrderMediaOverlay(false)` are defaults. The reflective `setFrameRate` is dead (see H3). Remove the block; upstream only calls `setFixedSize` pre-Marshmallow for 4K (`og:Game.java:938-955`).

#### L5. Per-event allocations in the touch path
- **Where:** `Game.java:2472-2515` (`sensitivityMap` keyed by `String.valueOf(pointerId)`, `SensitivityBean` lookups per move), `:2544, :2554, :2598, :2648` (`new float[2]` 2-3× per pointer per `MotionEvent`), `:3352` (`MotionEvent.obtain` on gesture cancel).
- **Mechanism (CONFIRMED):** the `float[]` allocations exist upstream too; the string-keyed map is fork-only and active only when touch sensitivity ≠ 100. All are small; worth scratch arrays/`SparseArray` but not a latency source. Historical samples *are* processed (`:3157-3190`), which is correct.

#### L6. `KeyboardAccessibilityService` filters every key event system-wide while enabled
- **Where:** `KeyboardAccessibilityService.java:52-61`, `res/xml/keyboard_accessibility_service.xml`.
- **Mechanism (CONFIRMED):** `FLAG_REQUEST_FILTER_KEY_EVENTS` routes every key event on the device through this service (binder hop into the app process, then back) before normal dispatch, whether or not a stream is active; `TYPES_ALL_MASK` + `canRetrieveWindowContent` subscribe to all accessibility events of the app's own package. Opt-in by the user; cost is one IPC per key event.

#### L7. Startup work on the main thread
- `Game.onCreate` constructs `MediaCodecDecoderRenderer` (`Game.java:652-671`), which enumerates `MediaCodecList` several times and — for HEVC/AV1 RFI detection — instantiates a real codec via `MediaCodec.createByCodecName` (`MediaCodecHelper.java:486-513`); this is upstream behavior (parity) but is tens to hundreds of ms on some SoCs (SUSPECTED). Fork-only additions: `ArtemisApplication.onCreate` → `ProfilesManager.load()` does a synchronous `FileReader` + Gson parse (`ProfilesManager.java:49-100`), and `PreferenceConfiguration.readPreferences` allocates an `OverlaySharedPreferences` wrapper per read (`:200-207`). All small.

#### L8. Leaked `DisplayManager.DisplayListener`
- **Where:** `Game.java:1011-1030`.
- **Mechanism (CONFIRMED):** `registerDisplayListener(new DisplayListener(){...}, null)` is never unregistered; the anonymous listener captures the `Game` activity, so when `enableFullExDisplay` is on and the stream runs on an external display, every finished `Game` instance stays reachable from `DisplayManagerGlobal` until process death.

#### L9. `Thread.sleep` on the UI thread in `handleButtonUp`
- `ControllerHandler.java:2476-2494` sleeps up to `MINIMUM_BUTTON_DOWN_TIME_MS` on the main thread for very short presses (parity with upstream; comment acknowledges it). Harmless unless an input device generates many sub-25 ms presses.

#### L10. "Warp" frame-pacing modes multiply the requested stream frame rate
- `Game.java:775-777` with `PreferenceConfiguration.java:866-871`: `warp` ⇒ `chosenFrameRate *= 2`, `warp2` ⇒ `*= 4`. The host encodes/transmits 2-4× frames; the decoder decodes all of them and the pacer discards most. This trades 2-4× decode/network/battery for lower perceived latency. Opt-in, but the Frame Pacing list (`arrays.xml:122-129`) does not say so.

#### L11. Parity notes requested by the brief
- `android:gwpAsanMode="always"` is in **both** manifests (upstream too) — sampled guarded allocations for every launch; a few hundred KB and negligible CPU. `ndk.debugSymbolLevel 'FULL'` is in both; it only affects the symbol bundle uploaded with an AAB, not the shipped `.so`.
- `Window.setPreferMinimalPostProcessing` is not called programmatically in either repo; both set `android:preferMinimalPostProcessing="true"` on the `Game` activity (ALLM). `setSustainedPerformanceMode` is used by neither (it lowers clocks for consistency, not a latency win).

## C. Already good (no action)

- **Surface type:** 2D path renders MediaCodec straight into a `SurfaceView` surface (`StreamContainer.java:83-84, :242`); no `TextureView`, no extra composition hop. `android:preferMinimalPostProcessing="true"` (ALLM) and `com.android.graphics.intervention.wm.allowDownscale=false` are set.
- **Input dispatch:** `requestUnbufferedDispatch` for button/joystick/pointer/position/trackball classes on both views (`Game.java:498-516`) plus per-`ACTION_DOWN` (`:3432`); touch history is consumed (`:3157-3190`); joystick history intentionally skipped (`ControllerHandler.java:1928-1929`); all `conn.send*` calls are single JNI calls that enqueue to common-c's sender thread (no socket I/O on the UI thread); `handleAxisSet` reuses a cached `Vector2d` (`:1704`).
- **Audio:** `AndroidAudioRenderer.java` is byte-identical to upstream: tries a 2-frame buffer first, `PERFORMANCE_MODE_LOW_LATENCY`/`FLAG_LOW_LATENCY`, `USAGE_GAME`, 16-bit PCM, skips low-latency when the HAL rate differs, caps queued audio at 40 ms via `LiGetPendingAudioDuration()`. Opus decoding is native (`callbacks.c:254-279`) with `GetPrimitiveArrayCritical` into a preallocated `short[]`.
- **Video JNI path:** one global reusable `byte[]`, one `SetByteArrayRegion` per NALU and one upcall per frame (+1 per CSD NALU) (`callbacks.c:146-201`), identical to upstream apart from ms-vs-µs timestamps; CSD batching, fused IDR with adaptive playback, RFI capabilities and the codec-recovery state machine (`:389-952`) match upstream.
- **Decoder selection:** two-round `findKnownSafeDecoder` preferring `FEATURE_LowLatency` decoders (`MediaCodecHelper.java:1009-1067`), software-only exclusion via `isSoftwareOnly()`/name heuristics, HEVC whitelist + media-performance-class shortcut, `low-latency` key tried first with FEATURE_LowLatency short-circuit (`:548-560`), vendor keys with progressive fallback tries, `KEY_OPERATING_RATE`/`KEY_PRIORITY=0` gated exactly as upstream (`:515-531, :581-590`). Fork additions (Qualcomm output-fence keys `:620-622`, NVIDIA/MTK presets) are additive and fall back on `configure()` failure. One caveat: the MTK preset sets `KEY_PRIORITY=0` + `KEY_OPERATING_RATE=MAX` unconditionally (`:669-670`), which upstream deliberately avoids on some SoCs — SUSPECTED risk, not a perf cost.
- **Display mode selection:** `prepareDisplayForRendering` (`Game.java:1455-1643`) and `surfaceCreated` (`:3811-3844`) are upstream's logic (mode chosen in `onCreate`, `preferredDisplayModeId` only when resolution changes or `enforceDisplayMode`, `setFrameRate` with `CHANGE_FRAME_RATE_ALWAYS` on S+).
- **Thread priorities:** render and Choreographer threads at `THREAD_PRIORITY_URGENT_DISPLAY` (`MediaCodecDecoderRenderer.java:1142, 1161`; upstream uses `DEFAULT + MORE_FAVORABLE`) — fine once the render thread blocks instead of polling (H2).
- **Connection lifecycle:** `NvConnection.start` runs the HTTP launch sequence on its own thread (`NvConnection.java:388-482`), `stop()` on a thread (`Game.java:3469-3482`), port tests on the callback thread; WifiLocks (`FULL_HIGH_PERF` + `FULL_LOW_LATENCY`) and `FLAG_KEEP_SCREEN_ON` are scoped to the activity/connection (`:546-557, :3703, :3573, :1718-1723`); sensors are unregistered on stop and in PiP (`ControllerHandler.java:304-320, :3214-3241`); battery polling is on a `HandlerThread` every 120 s.
- **PC/app browsing:** `ComputerManagerService` polls on background threads (1.5 s server-info, 30 s app list, mDNS 1 s) and stops in `onPause`/`onStop`; `CachedAppAssetLoader` uses bounded executors (3/3/1 threads, bounded queues, `DiscardOldestPolicy`), an `LruCache` sized `maxMemory/16` and a 5 MB per-asset cap with `inSampleSize` scaling. No `StrictMode` violations found; the only main-thread `commit()` is the crash tombstone (intentional, about to crash).
- **OSC rendering:** `Paint`/`RectF` are fields, `invalidate()` only on state change (`VirtualControllerElement.java:46`, `DigitalButton.java:59-60`, `AnalogStick.java:124`).
- **Native build hygiene:** 16 KB page support, `LOCAL_BRANCH_PROTECTION := standard`, `-Wl,--exclude-libs,ALL`, `-DLC_DEBUG` only under `NDK_DEBUG=1`. libopus prebuilt is 1.5.2, float (`FLP`) build with NEON intrinsics/asm and RTCD (`opus_select_arch`, `celt_pitch_xcorr_neon`, `silk_NSQ_del_dec_neon`, armv7 `_edsp`), SSE/AVX2 dispatch on x86 (inspected with `nm`/`strings`).
- **3D assets:** the 17.7 MB `.tflite` is mmap'd lazily (`Stereo3DRenderer.java:746-753`) only when 3D is active; LiteRT GPU delegate preferred with NNAPI/CPU fallbacks.
- **Leaks:** `Game.instance` and `ExternalDisplayControlActivity.instance` are nulled in `onDestroy`; `MoonBridge.cleanupBridge()` clears the static renderer/listener refs on stop.

## D. Build flags / toolchain vs upstream

| Item | Fork (jlobue10 / Artemis 20.2.6) | Upstream og/master (Moonlight 12.2) | Perf relevance |
|---|---|---|---|
| NDK | `27.0.12077973` | `29.0.14206865` | None measured; r29 only needed for `CINNAMON_BUN` APIs via compileSdk 37 |
| AGP / Gradle | 8.13.0 / 8.13 | 9.4.0 / 9.7.1 | R8 full mode default in both (AGP ≥ 8) |
| compileSdk / targetSdk / minSdk | 36 / 34 / 21 | 37 / 36 / 21 | compileSdk 36 blocks porting `setProducerThrottlingEnabled` (H4) |
| Java | 11 | 17 + core library desugaring | None |
| `minifyEnabled` (release) | true | true | — |
| Release ProGuard base file | `proguard-android.txt` (**`-dontoptimize`**) | `proguard-android-optimize.txt` | L1 |
| `-dontobfuscate` | yes | yes | — |
| Extra `-keep` | `org.opencv.**`, `org.tensorflow.lite.gpu.**`, `com.github.mikephil.charting.**`, Gson models | only Moonlight/JNI/BouncyCastle | L1 (DEX size) |
| ABI splits | `x86, x86_64, armeabi-v7a, arm64-v8a` (no universal) | none (single APK/AAB) | OpenCV AAR ships all four ABIs (x86_64 `.so` = 55.8 MB), so x86 splits are huge |
| `ndk.debugSymbolLevel` | FULL | FULL | size of symbol bundle only |
| `gwpAsanMode` | always | always | parity |
| `android:isGame` / `appCategory` | **absent** | `true` / `game` | H5 |
| `uses-feature glEsVersion` | 0x30000 required | not set | install filter only |
| `APP_OPTIM` / `APP_CFLAGS` / `-O` | not set (ndk-build defaults: release `-O2 -DNDEBUG`, debug `-O0`) | same | parity; no LTO, no `-ffast-math`, no `-g` in release in either |
| `LOCAL_CFLAGS` (moonlight-core) | `-DHAS_SOCKLEN_T=1 -DLC_ANDROID -DHAVE_CLOCK_GETTIME=1` | identical | parity |
| Branch protection / 16 KB pages | `standard` / `APP_SUPPORT_FLEXIBLE_PAGE_SIZES := true` | identical | parity |
| Reed-Solomon FEC | `moonlight-common-c/reedsolomon/rs.c` (scalar) | `nanors` (SIMDe NEON / GFNI dispatch) | M3 |
| moonlight-common-c | ClassicOldSong fork `c999436` (upstream + `LiSendEmptyPayload`) | `874ac954` | M3, M5 |
| libopus | prebuilt 1.5.2 float, NEON+RTCD (`libopus/`, moonlight-mobile-deps) | prebuilt in `opus/` (same origin per `Build.txt`) | parity |
| Static libs linked | `libopus libssl libcrypto cpufeatures` | `libopus libcrypto cpufeatures` | none (no `SSL_*` references; archive members unused) |
| Extra Java deps | Gson, AndroidX appcompat/preference/recyclerview/material, MPAndroidChart, LiteRT 1.4.0 (+GPU), OpenCV 4.12.0, SearchPreference | — | H6 (OpenCV load), L1 |
| Assets | `midas-midas-v2-w8a8.tflite` 17.7 MB (uncompressed, mmap'd lazily) | — | APK size only |

### Notes on the two upstream commits of special interest
- **`6d4c64a5` "Disable producer throttling on the video surface"** — adds `holder.getSurface().setProducerThrottlingEnabled(false)` in `surfaceCreated` when `SDK_INT >= CINNAMON_BUN` (Android 17 / API 37). Not present in the fork; needs compileSdk 37 (or reflection) to port. See H4.
- **`68adf9ec` "Disable H.264 constraint and level_idc modifications on Oreo and later"** — version-gates `doProfileSpecificSpsPatching`'s `constraintSet4/5 = false` branch and the `level_idc` rewrite to `< O`. The fork still performs both on every H.264 IDR SPS (`MediaCodecDecoderRenderer.java:1736-1740, :1917-1936`); the two hunks apply directly. See M1.
