# Picture-quality audit — jlobue10/moonlight-android (Artemis `moonlight-noir`, versionName 20.2.6, == upstream Artemis c5cf27f)

Scope: codec negotiation, HDR, colour, resolution/refresh, bitrate/stream config, audio, render path, perf stats.
Baseline for comparison: `og/master` = moonlight-stream/moonlight-android v12.2 (Sept 2026). All paths are under
`/home/user/jlobue10/moonlight-android/app/src/main/` unless stated. Line numbers are from the audited tree.
Every "upstream" claim was checked with `git diff -w og/master -- <file>` or `git show og/master:<file>`.
Apollo host behaviour was checked against `ClassicOldSong/Apollo` master (`src/nvhttp.cpp`, `src/rtsp.cpp`) fetched during the audit.

Legend: **CONFIRMED** = established by reading the code/arithmetic; **SUSPECTED** = the code path is confirmed but the
visible effect depends on device/host behaviour that needs a real-device test.

---

## (A) Executive summary

Artemis keeps upstream's fundamentally sound pipeline (SurfaceView → MediaCodec with FEATURE_LowLatency, correct HDR10
static-metadata plumbing, 709/limited-or-full range signalling, Qt-derived bitrate defaults, FEC-aware bitrate, PCM16
low-latency AudioTrack with correct 5.1/7.1 masks, `preferMinimalPostProcessing` for TVs). The quality regressions are
concentrated in the Artemis-only "latency" rework of the render loop and in a handful of Artemis features whose plumbing
is inconsistent with the rest of the pipeline. Nothing in the fork improves raw picture quality relative to upstream;
several things make frame delivery less regular, and a few options are dead or inverted.

Top 5 improvements (highest impact first):

1. **Stop forcing `framePacing = BALANCED` for every user** (`java/com/limelight/Game.java:694-702`). The Frame-pacing
   list (latency / balanced / cap-FPS / smoothness) is a no-op today; only the two "Warp" entries do anything. Restore
   upstream semantics (or map the LFR checkbox onto upstream's existing modes) so "Prefer smoothest video" actually
   produces smooth video and "Balanced with FPS limit" actually caps FPS.
2. **Make the render loop single-path.** In the default configuration the Artemis "latest-only" drain runs *and* the
   Choreographer queue runs (`binding/video/MediaCodecDecoderRenderer.java:1212-1247` vs `:1430-1449`, `:1069-1113`).
   Under any output burst the newer frame is presented before the older one (frame-order inversion / double-present),
   rendered-FPS stats are wrong, and `INFO_OUTPUT_FORMAT_CHANGED` is swallowed. Either the fast path must own all
   frames or it must be removed (upstream's loop is already the "latest-only" design for non-balanced modes).
3. **Delete `applySurfaceFrameRate()`** (`MediaCodecDecoderRenderer.java:611`, `:2412-2419`) and the dead reflection
   block in `Game.java:904-932`. The decoder's late `Surface.setFrameRate(streamFps, COMPATIBILITY_DEFAULT)` overrides
   `Game.surfaceCreated()`'s deliberate `setFrameRate(desiredRefreshRate, FIXED_SOURCE, CHANGE_FRAME_RATE_ALWAYS)`
   (`Game.java:3836-3842`), undoing the "highest refresh that is a multiple of the stream FPS" policy and the
   "Reduce refresh rate" option, and sends nonsense (e.g. 59940 Hz) for fractional custom rates.
4. **Fix the external-display mode's refresh/HDR plumbing** (`Game.java:410`, `:603-606`): a TV reporting
   60.000004 Hz becomes `fps = 60000` on the wire (`nvstream/StreamConfiguration.java:181-187`,
   `nvstream/http/NvHTTP.java:857-861,881`), which only Apollo decodes as millihertz; HDR is forced on without
   checking the external display's HDR capabilities, so SDR monitors get a tone-mapped PQ stream and the host desktop is
   switched to HDR.
5. **Port upstream 68adf9ec** ("Disable H.264 constraint and level_idc modifications on Oreo and later") and
   **make "Allow AV1 by default" actually do something**: 404d70db only flipped a whitelist that is never consulted in
   Auto mode (`MediaCodecDecoderRenderer.java:329-333`), so AV1 is still never used unless forced — on SD 8 Gen 2/3,
   Dimensity 9200+, Tensor G3+ class devices that is the best codec per bit the host can offer.

---

## (B) Findings (ordered by impact)

### HIGH

#### H1. The Frame-pacing preference is dead: every mode is forced to BALANCED
- `Game.java:690-704` (added by Artemis commit 8cc3323c "Add forceTightThresholds option to reduce latency"):
  ```java
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
  Both branches overwrite the user's choice *before* it is consulted anywhere: the cap-FPS block at `Game.java:757-770`
  (`if (prefConfig.framePacing == FRAME_PACING_CAP_FPS)`) can never run; `mayReduceRefreshRate()` (`:1445`) reduces
  to "BALANCED && reduceRefreshRate"; in the renderer `startChoreographerThread()` (`:1136`) always starts the
  Choreographer and the non-balanced branch at `:1284` (`if (prefs.framePacing != FRAME_PACING_BALANCED)`) — which
  contains all of the smoothness / cap-FPS / "never drop" logic — is unreachable. The only values that survive are the
  `warp`/`warp2` multipliers (`preferences/PreferenceConfiguration.java:866-871`) and the name written to the perf log.
- Effect: "Prefer lowest latency", "Balanced", "Balanced with FPS limit" and "Prefer smoothest video"
  (`res/values/arrays.xml:122-137`, `strings.xml:359-362`) all behave identically. Users who pick "smoothest" still get
  the 2-deep drop queue; users who pick "FPS limit" never get the `refreshRate-1` request that makes the stream fit the
  panel.
- **CONFIRMED** (code).
- Upstream: `og/master` `Game.java:447` keeps `prefConfig.framePacing` from preferences; `MediaCodecDecoderRenderer`
  (`og/master` lines 1031-1052) switches per mode (min-latency = `releaseOutputBuffer(idx, System.nanoTime())`,
  smoothness/cap = `releaseOutputBuffer(idx, 0)`, balanced = Choreographer).
- Fix: remove the two `prefConfig.framePacing = ...BALANCED` assignments; let `preferLowerDelays` be an additional knob
  on top of the chosen mode (or hide the list if Artemis intends only two modes).

#### H2. Default render loop runs two presentation paths at once → frame-order inversion, double-presents, wrong stats
- Fast path (`MediaCodecDecoderRenderer.java:1212-1247`, active when the "LFR" checkbox is **off**, i.e. the default):
  ```java
  /* LATEST_ONLY_LOW_LATENCY */
  if (!preferLowerDelays) {
      ...
      int __idx = videoDecoder.dequeueOutputBuffer(__tmpInfo, 0);
      // Drain non-blocking; keep only the newest buffer
      while (__idx >= 0) { ... videoDecoder.releaseOutputBuffer(__last, false); ... __idx = videoDecoder.dequeueOutputBuffer(__tmpInfo, 0); }
      if (__last >= 0) {
          releaseWithPolicy(__last, System.nanoTime());   // -> releaseOutputBuffer(idx, now)
          ...
          continue; // handled this iteration
      }
  ```
  Regular path (`:1252` `dequeueOutputBuffer(info, 2000µs)`, then `:1430-1449`): because of H1 the frame is pushed to
  `outputBufferQueue` and presented later by `doFrame()` with `releaseOutputBuffer(next, frameTimeNanos)` (`:1097`).
- Consequence: whenever two output frames are pending (jitter burst, 120 FPS stream, decoder catch-up), frame N is
  queued for the next Choreographer tick, the loop comes round, the fast path grabs N+1 and presents it immediately,
  then the Choreographer presents the *older* N one vsync later → visible backwards step / judder. The fast path also
  never updates `lastRenderedFrameTimeNanos` (the 80 %-of-period gate at `:1082`), so a Choreographer present can
  follow a fast-path present within the same frame period; it does not increment `totalFramesRendered`
  (`:1428`/`:1113` only), so "Rendering frame rate" under-reports; and a `-2 INFO_OUTPUT_FORMAT_CHANGED` returned by
  the non-blocking dequeue is silently discarded (`__idx >= 0` test), so `outputFormat` can stay null.
- **CONFIRMED** mechanism; **SUSPECTED** frequency (needs a device + jittery link to quantify).
- Upstream: one path per mode (`og/master` renderer 1019-1105).
- Fix: make the fast path exclusive (when it is enabled never enqueue to `outputBufferQueue`, or drain `outputBufferQueue`
  first and release those buffers without render), update `lastRenderedFrameTimeNanos`/`totalFramesRendered`, and
  handle `INFO_OUTPUT_FORMAT_CHANGED`. Better: revert to upstream's loop and expose "latest-only" as upstream's
  `FRAME_PACING_MIN_LATENCY`, which is the same algorithm.

### MEDIUM

#### M1. Decoder's `applySurfaceFrameRate()` overrides the activity's deliberate `Surface.setFrameRate()`
- `Game.java:3820-3842` (upstream logic): picks `desiredFrameRate` = the display refresh chosen by
  `prepareDisplayForRendering()` (e.g. 120 for a 60 FPS stream on a 120 Hz panel, "ensures the lowest possible display
  latency") and calls `setFrameRate(desiredFrameRate, FRAME_RATE_COMPATIBILITY_FIXED_SOURCE, CHANGE_FRAME_RATE_ALWAYS)`.
- `MediaCodecDecoderRenderer.java:611` (runs later, inside `configure()`, on every codec (re)start):
  ```java
  try { applySurfaceFrameRate(renderTarget, targetFps); } catch (Throwable ignored) {}
  ...
  surface.setFrameRate((float) targetFps, android.view.Surface.FRAME_RATE_COMPATIBILITY_DEFAULT);   // :2417-2418
  ```
  `targetFps` is the stream FPS as received by `setup()` (`:799`), i.e. 60 — or 59940 for a fractional custom rate.
- Effect: the last call wins. On LTPO/120 Hz phones the OS is now free to drop to 60 Hz for a 60 FPS stream
  (`COMPATIBILITY_DEFAULT`, seamless-only), which the user can no longer control with "Reduce refresh rate"; with
  fractional rates the requested value is nonsense. The second Artemis attempt (`Game.java:904-932`,
  `SurfaceView.class.getMethod("setFrameRate", float.class, int.class)`) always throws `NoSuchMethodException` and is
  swallowed — `SurfaceView` has no such method — so it is dead code.
- **CONFIRMED** ordering; **SUSPECTED** per-device outcome.
- Fix: delete `applySurfaceFrameRate()` and the reflection block; `Game.surfaceCreated()` already does the right call.

#### M2. H.264 SPS patching diverges from upstream 68adf9ec (constraint flags and level_idc still rewritten on Oreo+)
- `MediaCodecDecoderRenderer.java:1726-1740`:
  ```java
  if (sps.profileIdc == 100 && constrainedHighProfile) { ... }
  else {
      // Force the constraints unset otherwise (some may be set by default)
      sps.constraintSet4Flag = false;
      sps.constraintSet5Flag = false;
  }
  ```
  and `:1917-1944` (`if (!refFrameInvalidationActive) { ... "Patching level_idc to 42" ...}`).
- Upstream (`git show og/master 68adf9ec`, Sept 2026) changed both to `else if (Build.VERSION.SDK_INT < O)` and
  `if (!refFrameInvalidationActive && Build.VERSION.SDK_INT < O)`, leaving modern decoders the SPS the encoder wrote.
  `num_ref_frames = 1` (`:1947-1949`) and the bitstream-restriction injection (`:1966`) are still shared with upstream.
- Effect: every IDR SPS on Oreo+ is rewritten (level 4.2 for 1080p60, constraint_set4/5 cleared). Decoders that size
  their DPB from `level_idc` may behave differently from the encoder's intent; upstream judged the hacks obsolete.
- **CONFIRMED** divergence; **SUSPECTED** visible effect (device dependent).
- Fix: port the two-line change from 68adf9ec.

#### M3. Reference-frame invalidation force-enabled on all MediaTek and `c2.qcom` decoders
- `MediaCodecHelper.java:405-409` (Artemis, "derflacco"):
  ```java
  refFrameInvalidationAvcPrefixes.add("c2.mtk"); //derflacco
  refFrameInvalidationHevcPrefixes.add("c2.mtk"); //derflacco
  refFrameInvalidationAvcPrefixes.add("omx.mtk"); //derflacco
  refFrameInvalidationHevcPrefixes.add("omx.mtk"); //derflacco
  refFrameInvalidationHevcPrefixes.add("c2.qcom"); //derflacco
  ```
  Upstream only enables MTK RFI on Fire OS devices and on PowerVR GX6 parts (`:442`), with the comment "RFI on HEVC
  causes decoder hangs on the newer GE8100, GE8300, and GE8320 GPUs" and "AVC RFI ... adds a huge amount of latency" on
  MT8176. With RFI advertised, the client also skips the `num_ref_frames=1` patch (`:1947`) and tells the host
  `x-nv-video[0].maxNumReferenceFrames=0` (unlimited; `moonlight-common-c/src/SdpGenerator.c:466-473`), i.e. up to 16
  references — exactly errata #1/#13/#14 in `decoder-errata.txt`.
- Quality upside when it works: no full-IDR after loss (less pixelation) and better compression; downside on affected
  MTK parts: multi-hundred-ms latency or hangs, and `consecutiveCrashCount % 2 == 1` (`:450`) only disables RFI on
  every other launch.
- **CONFIRMED** code; **SUSPECTED** device impact.
- Fix: gate MTK RFI the way HEVC already does (`decoderSupportsRefFrameInvalidationHevc`: FEATURE_LowLatency or a
  known vendor LL key) instead of a blanket prefix, or allow-list by `Build.SOC_MODEL`.

#### M4. "Allow AV1 by default" (404d70db) is a no-op — AV1 is never used in Auto
- `MediaCodecDecoderRenderer.java:329-333`:
  ```java
  private MediaCodecInfo findAv1Decoder(PreferenceConfiguration prefs) {
      // For now, don't use AV1 unless explicitly requested
      if (prefs.videoFormat != PreferenceConfiguration.FormatOption.FORCE_AV1) {
          return null;
      }
  ```
  The commit flipped `isDecoderWhitelistedForAv1()` to `return true` (`MediaCodecHelper.java:873-874`), but that
  function is only reached after the `FORCE_AV1` gate, and in `FORCE_AV1` mode a non-whitelisted decoder is used anyway
  ("Forcing AV1 enabled despite non-whitelisted decoder"). So `supportedVideoFormats` (`Game.java:723-734`) only gets
  `VIDEO_FORMAT_AV1_MAIN8/10` when the user forces AV1.
- Effect: on every AV1-capable phone the host (Sunshine ≥ 0.21 / Apollo with NVENC/AMF/QSV AV1) encodes HEVC in Auto;
  AV1 gives noticeably fewer artifacts at the same bitrate (~20-30 % more efficient than HEVC on NVENC AV1), which is a
  pure picture-quality loss at the common 20-40 Mbps settings.
- **CONFIRMED**. Upstream: identical gate (`og/master` 240-244), so this is an inherited limitation plus a misleading
  commit. moonlight-qt (from memory): uses AV1 automatically when hardware AV1 decode is available and the host
  advertises it.
- Fix: in Auto, call `findAv1Decoder` when `isDecoderWhitelistedForAv1()` holds *and* the decoder is hardware with
  `FEATURE_LowLatency` or `MEDIA_PERFORMANCE_CLASS >= 33`; keep the perf-point fallbacks that already exist in the
  function.

#### M5. HDR gating is tied to the wrong display; external-display mode forces HDR without a capability check
- `Game.java:603-606`:
  ```java
  if (onExternelDisplay) {
      // Enforce HDR on unsupported hardware can still enable 10bit streaming for better quality
      willStreamHdr = true;
  }
  ```
  No `currentDisplay.getHdrCapabilities()` check for the external display; the internal path (`:609-626`) does check.
  Meanwhile `preferences/StreamSettings.java:456` + `:640-660` hides the "Enable HDR" checkbox based on
  `activity.getWindowManager().getDefaultDisplay()` — the *phone* panel — so a phone with an SDR panel plugged into an
  HDR monitor cannot enable HDR at all, and a phone with an HDR panel plugged into an SDR monitor gets HDR forced.
- The comment's premise is wrong: there is no "10-bit SDR" in the protocol — negotiating a 10-bit format makes
  common-c send `x-nv-video[0].dynamicRangeMode=1` (`SdpGenerator.c:455-460`) and the client sends `hdrMode=1`
  (`NvHTTP.java:886`), so Apollo/Sunshine switch the host display/game to HDR and encode PQ/BT.2020. On an SDR external
  display Android must tone-map the PQ layer (SurfaceFlinger/HWC; quality is vendor-specific, frequently dim or
  clipped), and the host desktop stays in HDR.
- **CONFIRMED** code paths; **SUSPECTED** visual result on SDR monitors.
- Upstream: no external-display mode; HDR gated on the stream display's capabilities only.
- Fix: run the same `getHdrCapabilities()` loop on `currentDisplay` in the external branch; in Settings, evaluate the
  display that `ServerHelper.getActiveDisplay()` returns (it already exists in `utils/ServerHelper.java:64-71`).

#### M6. "LFR (Experimental)" checkbox is inverted relative to its description and its own code comments
- `strings.xml:728-729`: "LFR (Experimental) — Minimizes delay by dropping queued frames."
- `MediaCodecDecoderRenderer.java:46-48` comment: "Set true to enable a 'latest-only' fast path"; `:1213`:
  `if (!preferLowerDelays) { /* LATEST_ONLY_LOW_LATENCY */`. `Game.java:694` sets `setPreferLowerDelays(true)` when the
  box is ticked. So: **off** (default) = drain-to-newest fast path + Choreographer (H2); **on** = fast path disabled,
  Choreographer-gated frames released immediately (`releaseWithPolicy()` → `releaseOutputBuffer(idx, true)`,
  `:67-72`), dequeue timeout 500 µs instead of 2000 µs (`:83`).
- Effect: ticking the box to "drop queued frames" removes the only code that drops queued frames (apart from the
  2-deep producer-side limit) and changes vsync alignment from timestamp-scheduled to immediate.
- **CONFIRMED**.
- Fix: decide which behaviour the option means and make the condition match; document the default.

#### M7. Warp modes multiply the requested stream FPS by 2x/4x without touching the bitrate
- `PreferenceConfiguration.java:866-871` (`warp` → 2, `warp2` → 4); `Game.java:775-776`:
  ```java
  if (prefConfig.framePacingWarpFactor > 0) {
      chosenFrameRate *= prefConfig.framePacingWarpFactor;
  }
  ```
  → `setRefreshRate(120|240)` while `setLaunchRefreshRate(prefConfig.fps)` stays 60; bitrate unchanged
  (`setBitrate(prefConfig.bitrate)`, `:790`).
- Host side: Apollo detects the ratio (`rtsp.cpp:1037-1040`, `:1060-1064` "Hack: Restore bitrate for warp mode ...
  configuredBitrateKbps *= warp_factor") **only when its `limit_framerate` setting is on**; otherwise, and on
  Sunshine/GFE always, the encoder is asked for 120/240 FPS at the 60 FPS bitrate — each frame gets half/quarter of the
  bits (visible blocking in motion), the client then discards half/three-quarters of them (2-deep queue), and the
  default-bitrate formula's >60 FPS damping (`:508`) is bypassed.
- **CONFIRMED** client arithmetic; **SUSPECTED** severity (depends on host and `limit_framerate`).
- Fix: scale `bitrate` by the warp factor client-side (or only offer Warp when the host is Apollo — serverinfo exposes
  `VirtualDisplayCapable`), and show the real request in the perf overlay.

#### M8. External-display mode: fractional panel refresh → `fps × 1000` on the wire and in the decoder
- `Game.java:410`: `prefConfig.fps = currentMode.getRefreshRate();` (TVs/monitors commonly report 60.000004 or
  59.940063). `StreamConfiguration.java:181-187`:
  ```java
  if (refreshRate == (int)refreshRate) { return (int)refreshRate; } else { return (int)(refreshRate * 1000); }
  ```
  so `MoonBridge.startConnection(... fps=60000 ...)`, `x-nv-video[0].maxFPS=60000` (`SdpGenerator.c:319-320`),
  `&mode=WxHx60000` (`NvHTTP.java:857-861,881`), and on the client `setup(..., redrawRate=60000)` →
  `KEY_FRAME_RATE=60000` (`:540`), `targetFps=60000` → `Surface.setFrameRate(60000f)` (M1), 80 % gate = 13 µs
  (`:1082`), `streamPeriodNs` = 16.7 µs in the pacing EWMA (`:1177-1180`).
- Apollo handles it: `nvhttp.cpp:436-441` (`if (fps < 1000) fps *= 1000`) and `rtsp.cpp:1026-1040` treat ≥1000 as
  millihertz. Sunshine and GFE have no such convention → a 60000 FPS encoder/capture configuration or a refused launch.
  The custom-refresh-rate feature is documented "Only supports Apollo" (`strings.xml:433`), but external-display mode
  triggers it silently for every non-integer TV mode.
- **CONFIRMED** client path and Apollo handling; **SUSPECTED** Sunshine outcome (not testable here).
- Fix: `Math.round()` the external mode rate when `|fps - round(fps)| < 0.05`; only emit the ×1000 encoding when the user
  typed a fractional custom rate (and ideally only for Apollo); keep the decoder-side values in Hz.

#### M9. AI SBS-3D path: 8-bit SDR only, `mediump` texture sampling, synchronous read-backs, movie mode stalls the GL thread
- `ui/StreamContainer.java:86-92` creates a `GLSurfaceView` (ES3, default EGL config → 8-bit RGB) and the decoder renders
  into a `SurfaceTexture` (`utils/Stereo3DRenderer.java:238-240`); no HDR dataspace survives, so 3D is SDR-only and a
  10-bit stream would be sampled through `samplerExternalOES` with vendor-defined conversion.
- `utils/ShaderUtils.java:15` (`FRAGMENT_SHADER_3D`) and `:196`: `precision mediump float;` with `v_TexCoord` used for
  `texture2D(s_ColorTexture, ...)` on a 1920-3840 px wide source. Where mediump is fp16 (Mali/Adreno fragment stages),
  UV resolution is ~1/2048 → sampling positions are off by up to ~1 px across the frame (softening / aliasing of fine
  detail) — **SUSPECTED**, GPU dependent.
- `Stereo3DRenderer.java:645-657` `readPixelsForAI()` does a blocking `glReadPixels` of the 256×256 FBO every rendered
  frame (`:447`); the PBO double-buffer version `readPixelsForAI_Async()` (`:660-690`) exists but is unused.
- Movie mode (`render_mode_list=2`): `:463-470` `while ((newMap = latestDepthMap.getAndSet(null)) == null) Thread.sleep(1);`
  blocks `onDrawFrame` until MiDaS finishes → render FPS = inference FPS (MiDaS v2 int8 at 256×256; GPU delegate with
  `setPrecisionLossAllowed(true)` `:695`, NNAPI, then CPU fallback) and an unbounded hang if `tflite == null`
  (`AiTask.run` returns immediately at `:990`). Each eye is drawn at half the view width (`:354-357`), inherent to SBS.
- The depth map is 256×256 bilinearly upsampled (`GL_LINEAR`, `:806-807`) and blurred before DIBR; the scene-change
  heuristic (`ON_DRAW_CHANGE_TRESHOLD = 2.0f`, `:129`) reuses the previous depth map for small changes — fine.
- **CONFIRMED** structure.
- Fix: `precision highp float` in the 3D fragment shaders (at least for `v_TexCoord`/UV math), use the async PBO path,
  bound the movie-mode wait, and state in the Render Mode summary that 3D is SDR/8-bit.

### LOW

#### L1. Perf overlay ("Simplified performance information") mislabels what it measures
- `MediaCodecDecoderRenderer.java:1808-1818`: "Packet loss" prints `framesLost / totalFrames` (frame loss, not packet
  loss); "FPS" prints `fps.totalFps` = `totalFrames/elapsed` where `totalFrames` *includes lost frames*
  (`:1758-1760`), i.e. the host send rate, not what was rendered; "Network/Decoding delay" = ENet RTT estimate / mean
  enqueue→dequeue time (`updateDecodeLatencyStats`, `:86-98` — this part is an improvement over upstream's
  `uptime - PTS`). The big overlay's "Rendering frame rate" misses fast-path presents (H2). **CONFIRMED.**

#### L2. "Decoder watchdog for C2 sleep" never runs
- `:1479-1495` sits *after* the `while (!stopping)` loop closes (`:1477`), and `lastOutputNs` is never updated inside
  the loop. The flush/`priority` poke only executes once at shutdown. **CONFIRMED** dead code (claimed feature in
  commit 3c8b9600).

#### L3. "Tight Vsync (Experimental)" checkbox is dead
- `res/xml/preferences.xml:74-79` (`checkbox_forceTightThresholds`) is never read by `PreferenceConfiguration`
  (`:235` is a bare field, always false); `Game.java:673-690` reads that field via reflection; the renderer stores it
  (`:53-55`) and never uses it in the loop. **CONFIRMED.**

#### L4. `enqueueNsByPtsUs` grows without bound on dropped frames
- `:1688` inserts every input PTS; entries are only removed by `updateDecodeLatencyStats()`. Buffers released without
  render by the fast path (`:1223`), by the producer-side queue limit (`:1441`) or on recovery are never pruned. Boxed
  `Long` per dropped frame — hours of a lossy session leak megabytes and slow the sparse-array binary search.
  **CONFIRMED.**

#### L5. Portrait auto-invert swaps the decoder dimensions twice
- `Game.java:429-432` sends the swapped size to the host (`setResolution(displayWidth, displayHeight)` = 1080×1920) and
  also passes `shouldInvertDecoderResolution` to the renderer, whose `setup()` swaps the already-swapped values back
  (`:800-801`: `initialWidth = invertResolution ? height : width`). The `MediaFormat` (and `KEY_MAX_WIDTH/HEIGHT` for
  adaptive playback, `:544-546`) is therefore 1920×1080 for a 1080×1920 bitstream; decoders re-derive the real size from
  the SPS, but the adaptive-playback maximum is violated and the perf overlay/crash report show the transposed size.
  **CONFIRMED** code; **SUSPECTED** decoder impact (strict decoders may reconfigure on the first IDR).

#### L6. `setFixedSize()` re-introduced on the stream SurfaceView, with the un-inverted size
- `Game.java:896-902`: `streamSurfaceView.getHolder().setFixedSize(prefConfig.width, prefConfig.height)` ("fixed size +
  pacing without back-pressure on MTK"). For a MediaCodec-fed surface the producer sets buffer dimensions itself, so this
  is at best redundant; in portrait it is transposed (see L5); and the fork's own code at `:1618` says "Don't do
  setFixedSize since it might not update the view dimensions correctly when entering PiP mode". Upstream removed
  `setFixedSize` on M+. **CONFIRMED** contradiction; **SUSPECTED** no visible effect.

#### L7. Custom resolution / bitrate inputs are barely validated
- `StreamSettings.java:905-912` accepts any `width>0 && height>0`: odd values are rounded down by common-c
  (`Connection.c:300-309`) while `displayWidth/Height` and the aspect-ratio math keep the odd value (1 px mismatch →
  non-1:1 scaling); nothing checks the decoder's `getSupportedWidths()`/H.264 4096 limit (the stream then fails at
  `initializeDecoder()` `:701-703`). `:865-878` accepts up to 5 digits of Mbps (99 999 Mbps) and writes it straight to
  `seekbar_bitrate_kbps` (seekbar max is 300 000 kbps, `preferences.xml:30-40`; upstream's max is 150 000). **CONFIRMED.**

#### L8. HDR10 profile detection requires the exact `*Main10HDR10` constants
- `:464-495`: only `HEVCProfileMain10HDR10` / `AV1ProfileMain10HDR10` count. A decoder that advertises
  `HEVCProfileMain10` (+`Main10HDR10Plus`) only is refused with "Decoder does not support HDR10 profile"
  (`Game.java:708-710`). Same as upstream; worth accepting `HEVCProfileMain10` and `AV1ProfileMain10` (Android decodes
  PQ/BT.2020 Main10 streams identically — the HDR10 profile flag only promises static-metadata handling). **CONFIRMED**
  logic; **SUSPECTED** how many devices it affects.

#### L9. 4:4:4 is unreachable (and partly unrepresentable) on this client
- common-c defines the five 4:4:4 formats (`Limelight.h:221-237`) and negotiates them (`RtspConnection.c:1076-1116`,
  `SdpGenerator.c:305-310` `x-ss-video[0].chromaSamplingType`), but `nvstream/jni/MoonBridge.java:13-22` has no
  `VIDEO_FORMAT_*_444` constants and defines `VIDEO_FORMAT_MASK_10BIT = 0x2200` (common-c: `0xAA00`), so
  `supportedVideoFormats` can never carry them and a future 10-bit-444 negotiation would be mis-detected as SDR by
  `createBaseMediaFormat()` (`:557`). Android's public `MediaCodecInfo.CodecProfileLevel` has no HEVC RExt or AV1
  High/Professional 4:4:4 constants and hardware decoders do not advertise `AVCProfileHigh444`; only a software decoder
  (ffmpeg/dav1d) could consume what Sunshine/Apollo offer. **CONFIRMED** — informational.

#### L10. 10-bit SDR cannot be requested (protocol), so "10-bit for less banding" is only available by enabling HDR
- Any 10-bit `VIDEO_FORMAT_*` sets `dynamicRangeMode=1` (`SdpGenerator.c:455-460`) and the launch carries `hdrMode=1`.
  Shared with upstream and moonlight-qt. The only client-side lever for banding is bitrate (full-range signalling,
  `checkbox_full_range`, is correctly plumbed to both encoder `encoderCscMode` and `KEY_COLOR_RANGE`,
  `SdpGenerator.c:533-534`, `:551-553`). **CONFIRMED** — informational.

#### L11. Audio path is byte-identical to upstream (no fork regressions) — two optional refinements
- `binding/audio/AndroidAudioRenderer.java` has an empty whitespace-insensitive diff vs `og/master`. PCM16
  (`opus_multistream_decode` → `short[]`, `jni/moonlight-core/callbacks.c:259`), `USAGE_GAME` (`:30`),
  `PERFORMANCE_MODE_LOW_LATENCY` only when the native rate matches 48 kHz (`:151`), 5.1 = `CHANNEL_OUT_5POINT1`, 7.1 =
  `0x18fc` (`:87`), both matching the Opus mapping order sent in `surroundAudioInfo` (`NvHTTP.java:889`). The 40 ms
  backlog policy (`:191`) *drops* a 5 ms Opus frame outright (audible tick under backlog) rather than resampling. Optional:
  `ENCODING_PCM_FLOAT` + `opus_multistream_decode_float` would skip one int16 requantisation before AudioFlinger's float
  mixer (marginal). **CONFIRMED.**

#### L12. Dead/unused vendor helpers that would hurt if wired up
- `MediaCodecHelper.java:1161-1190` `applyExtraVendorOptions()` (never called) sets
  `vendor.qti-ext-dec-frame-drop.enable=1` — a Qualcomm key that permits the decoder to drop frames. Leave it dead or
  remove it. `isMTKDecoderName()` (`:2428`) is also unused. **CONFIRMED.**

---

## (C) Capability matrix (what the client advertises / can decode / exposes)

| Codec / profile | Advertised to host (`supportedVideoFormats`) | Decodable on Android HW | Exposed in UI |
|---|---|---|---|
| H.264 High 4:2:0 8-bit | Always (`Game.java:723`) | Yes (required; `findAvcDecoder`) | "Force H.264" (`video_format=neverh265`) |
| H.264 High 4:4:4 8-bit | **No** — no `MoonBridge` constant | Practically no (`AVCProfileHigh444` exists but HW decoders don't advertise it) | No |
| HEVC Main 8-bit 4:2:0 | When a whitelisted/`FEATURE_LowLatency`/perf-class-12 HW decoder exists and not Force-H.264 (`:725`, `decoderIsWhitelistedForHevc`) | Yes | Auto / "Force HEVC" |
| HEVC Main10 (HDR10, PQ/BT.2020) | Only if `willStreamHdr` **and** `HEVCProfileMain10HDR10` (`:726-727`) | Yes on HDR-capable SoCs | "Enable HDR" (hidden unless the *default* display reports HDR10) |
| HEVC Main10 SDR | Not expressible (protocol: 10-bit ⇒ `dynamicRangeMode=1`) | — | — |
| HEVC RExt 4:4:4 8/10-bit | **No** | No public profile constant; no HW support | No |
| AV1 Main 8-bit | **Only when "Force AV1"** (`:329-333`, `:730-731`); never in Auto | Yes on SD 8 Gen 2+, Dimensity 9000+, Tensor G3+, Exynos 2200+ | "Force AV1" |
| AV1 Main10 (HDR10) | Force AV1 + HDR + `AV1ProfileMain10HDR10` (`:732-733`) | Yes on the above | same |
| AV1 High 4:4:4 8/10-bit | **No** | No | No |
| RFI (ref-frame invalidation) | AVC: qcom/nvidia/**all MTK** (M3); HEVC: exynos/qcom/**all MTK**/c2.qcom/Fire-Amlogic or any `FEATURE_LowLatency` decoder; AV1: `FEATURE_LowLatency`/vendor LL key only | — | No (auto; disabled on odd crash counts) |
| Colour | Rec.709 (Rec.601 pre-Oreo without HEVC), limited or full range → `encoderCscMode`; HDR colour taken from `dynamicRangeMode` on the host | `KEY_COLOR_STANDARD/TRANSFER` only set for SDR; HDR via VUI/SEI + `KEY_HDR_STATIC_INFO` restart | "Full range" checkbox |

---

## (D) Already good (keep as is)

- **SurfaceView (not TextureView) for the 2D stream** (`ui/StreamContainer.java:82-84`) — HDR passthrough and 1:1
  compositor scaling work; pan/zoom uses view `setScaleX/Y` on the SurfaceView (`utils/PanZoomHandler.java:131-136`),
  which Android N+ applies in the compositor (no re-encode, no software scaling).
- **`android:preferMinimalPostProcessing="true"`** on the Game activity (`AndroidManifest.xml:203`) → ALLM / game
  content type on HDMI TVs. Upstream lacks this.
- **HDR10 static metadata**: `SS_HDR_METADATA` → 25-byte CTA-861.3 blob, little-endian, R/G/B/W primaries, max/min
  mastering luminance, MaxCLL/MaxFALL (`MediaCodecDecoderRenderer.java:581-600`), applied through a codec restart
  (`setHdrMode`, `:1650-1676`) exactly like upstream; `KEY_COLOR_*` keys deliberately omitted for 10-bit so the decoder
  follows the bitstream VUI (`:557-567`); host GPU HDR capability check via SCM `0x20200` (`NvConnection.java:250-254`).
- **Decoder selection** prefers hardware decoders with `FEATURE_LowLatency` (`findKnownSafeDecoder` two-round scan),
  blacklists software-only decoders on Q+, whitelists HEVC via `MEDIA_PERFORMANCE_CLASS`/`FEATURE_LowLatency`; Balanced
  mode still uses the Choreographer with vsync-aligned `releaseOutputBuffer(idx, frameTimeNanos)` and the 80 %-of-period
  gate (`:1082`, `:1097`).
- **Display-mode selection** (`Game.java:1455-1600`) keeps upstream's refresh-rate matching heuristics (equal/good match,
  no resolution regression, 4K guard, `preferredDisplayModeId` only when resolution changes on S+) plus the Artemis
  `enforceDisplayMode` escape hatch for ColorOS-type ROMs.
- **Native-resolution handling**: cutout-aware entries (`StreamSettings.java:465-516`, `getRealMetrics` minus safe
  insets), `LAYOUT_IN_DISPLAY_CUTOUT_MODE_ALWAYS` for native/stretch (`Game.java:444-452`), and Fit/Fill/Stretch math
  (`StreamContainer.java:115-146`) — numerically verified during this audit to produce exact pixel sizes for the common
  20:9 / 16:10 / 16:9 panels (the `(int)` truncation only differs from rounding in cases that are non-1:1 anyway).
- **Stream config**: Qt-derived default bitrate with the >60 FPS square-root damping (`PreferenceConfiguration.java:498-530`),
  separate metered-network bitrate, `packetSize 1392` + LAN/VPN/NAT64 auto-detection (`NvConnection.java:130-230`),
  `clientRefreshRateX100` from the actual chosen refresh, encryption of everything when AES is hardware-accelerated
  (`callbacks.c:482-500`), FEC-aware 80 % video bitrate and full `configuredBitrateKbps` to Sunshine
  (`SdpGenerator.c:336-371`).
- **Apollo-specific plumbing** is correct and quality-positive: `virtualDisplay=1` creates a host display at the exact
  client resolution/refresh (1:1 pixels), `scaleFactor` (20-200 %) requests host-side supersampling
  (`NvHTTP.java:881-887`, `strings.xml:200`), `appuuid`, fractional refresh for Apollo.
- `requestUnbufferedDispatch` for input, `THREAD_PRIORITY_URGENT_DISPLAY` render/choreographer threads, and the
  enqueue→dequeue decode-time measurement (`:86-98`) are reasonable Artemis additions.
