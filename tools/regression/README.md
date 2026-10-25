# Host regression checks

Run each `test_*.py` with Python 3, JDK 17+ (`java` with the `jdk.compiler`
module), and g++ for the Vulkan test. The tests never connect to a controller,
start a stream, or load Android native libraries. They compile production
sources or extract production methods verbatim, with deterministic platform
boundaries. See each script for the exact boundary being replaced.

- `test_audio_passthrough.py`: the full `AndroidAudioRenderer` and `PassthroughOutput` with a
  fake bitstream encoder and scripted AudioTracks: IEC 61937 preferred over raw encodings,
  the TrueHD/DTS/AC-3 fallback chain, buffer sizing, frame-sized writes, partial-write
  retry, dead-output recreation keeping the encoder, PCM fallback, no effect session for
  a bitstream, and single release on cleanup. `test_passthrough_iec61937.py`: the C burst
  packer (preamble words, byte order, odd lengths, padding, bsmod, rejection).
- `test_touchpad_gestures.py`: the production `TouchpadGate` and `TrackpadGestureDetector`
  against scripted touchpad contacts, keys and a recording connection: typing pause and
  edge rejection, the Ctrl+Shift+M toggle, 3/4-finger swipes and taps as shortcuts, Alt
  released after an Alt+Tab switch, late and typing-guarded fingers ignored, pinch versus
  scroll, and reset releasing held modifiers. `test_trackpad_lifecycle.py` also covers the
  scroll-speed factor, the pointer-momentum setting, the move sink and double-tap jitter.
- `test_connection_ownership.py`: production connection start/stop; cancelling HTTP
  setup, stale/repeated stops, native-start failure cleanup, and interruption of a
  blocked native start without leaking the process-wide semaphore. Cancellation
  while queued for another session's permit must finish without stopping its owner.
- `test_pyrowave_recovery.py`: renderer rebuilds, cleanup/replacement during recovery,
  HDR restoration and the terminal error budget, with JNI calls recorded; a frame
  skipped for packet loss resets the streak and reads as dropped.
- `test_depth_failure_cleanup.py`: failed-model cleanup blocked across stop; the GL
  waiter exits while native cleanup remains on its owning executor.
- `test_controller_arrival_lifecycle.py`: actual USB listener/puck replay and
  controller slot allocation; duplicate arrivals retain the original host slot,
  disconnect/reconnect releases and reuses it, and admitted arrivals finish
  publishing before stop cleans up. `--baseline` reads HEAD.
- `test_controller_feedback_stop.py`: admitted rumble, Steam haptics/settings, sensor
  configuration and LED scheduling finish before stop cleans up device resources.
- `test_controller_motion.py`: actual motion rate handling; SensorManager cap, driver-fed
  250 Hz, burst-tolerant decimation with the production constants.
- `test_steam_ble.py`: actual BLE driver; queue limits, stale callbacks,
  disconnected rumble, subscription failures, write timeout recovery, and first
  state delivery across announcement/reconnect while deduplicating live packets.
- `test_jni_frame_buffer.py`: actual JNI setup, growth and cleanup methods with
  allocation failure injection; local/global reference ownership and retry after
  failed growth. PyroWave frames: whole delivery with the per-packet map (lost
  stand-ins, record starts, empty buffers, map growth and failure, cleanup).
  Requires JDK JNI headers (`AUDIT_JNI_INCLUDE` can override them).
- `test_nsd_discovery_lifecycle.py`: full Android NSD agent with delayed callbacks,
  duplicate and failed registrations, bounded tracking, and idle worker retirement.
- `test_ble_start_lifecycle.py`: actual activity driver-start method; late
  permission callbacks cannot start BLE or request permission during teardown.
- `test_surface_handoff.py`: actual surface handoff methods; UI dispatch,
  duplicate readiness and destruction overlapping a GL notification.
- `test_video_skip_stats.py`: actual frame statistics methods; native queue skips,
  true network loss, counter rollover and statistics-window aggregation.
- `test_stereo_workers.py`: actual worker and delegate setup methods;
  buffer ownership, failure cleanup and interruption.
- `test_pyrowave_fences.py`: entire native renderer compiled against vendored
  Vulkan declarations; fake Vulkan dispatch simulates consecutive acquire
  timeouts. Also checks three container formats, truncations and 20,000
  deterministic malformed frames under ASan/UBSan.
- `test_pyrowave_framing.py`: entire native renderer with a capturing PyroWave
  decoder stub; synthetic Vibepollo record frames with per-packet maps through
  the hole-tolerant walk: an intact frame feeds the decoder the same bytes as the
  strict whole-frame parser, lost detail packets drop only the damaged records and
  resync at the next record start (flagged and unflagged hosts), loss in the
  sequence header or critical prefix skips the frame as loss rather than error,
  padding is stripped and malformed records are rejected as before. ASan/UBSan.
- `test_pyrowave_state.py`: actual setup/HDR/cleanup methods; early HDR state.
- `test_depth_download.py`: actual downloader, fake HTTP streams and real files;
  cancellation overlapping a retry cannot delete the retry's private file.
- `test_xr_lifecycle.py`: actual presenter start/stop/failure methods; queued
  callbacks after stop/restart, synchronous capability grants and teardown errors.
- `test_stream_shutdown.py`: actual container teardown/fallback methods; delayed
  work after destruction and cleanup of a partly initialized stereo renderer.
- `test_depth_scratch.py`: production frame comparison; bounds application-level
  Mat construction to two headers per comparison and checks explicit release,
  including a failing Sobel operation. It does not measure native memory or FPS.
- `test_stereo_initialization.py`: actual surface lifecycle and session stop;
  a native model constructor ignores interruption while teardown cancels the GL
  wait. Checks late publication, deferred close and teardown inside the ready callback.
- `test_stereo_render_demand.py`: actual draw method and depth notification;
  idle draws do not submit duplicate input, late depth maps trigger redraws, and
  unchanged maps are not repeatedly uploaded.
- `test_stereo_scene_cache.py`: actual inference worker with a deterministic model;
  slow accumulated changes and producer-side skipped frames invalidate cached depth,
  identical frames reuse it, and owned buffers return on shutdown.
- `test_stereo_render_resources.py`: actual shader setup and timing accounting;
  shader/program cleanup on success and failure, millisecond draw durations, and
  frame rates normalized to the measured interval. GL and clock inputs are faked.

The `--baseline` option on supported scripts uses the current HEAD's source,
which is useful before committing local fixes. To compare published revisions,
use the test scripts from this change against a checkout of the PR base.

For restricted containers where LeakSanitizer cannot inspect `/proc`, run the
native check with `ASAN_OPTIONS=detect_leaks=0`. Address and undefined-behavior
sanitizers stay enabled; that invocation does not test native leaks.

These checks do not replace Android Gradle/NDK/R8 builds or BLE, XR, Vulkan,
HDR display, tensor-quality and thermal/performance tests on real devices.

`.github/workflows/pr-build.yml` runs these regressions, builds unsigned release
APKs for all supported ABIs (including NDK and R8), and runs Android unit tests.
`StereoSessionTest` runs the real stereo worker and teardown with native
inference/pixel boundaries mocked. It holds inference or initialization across
surface replacement and verifies buffer/flag isolation and deferred model close.

# Codec initialization and HDR launch selection

- `test_codec_fallback.py`: production low-latency option builder and retry loop
  with rejecting codecs; bounded MediaTek failure, NVIDIA option removal, and
  successful first-attempt behavior across codec families.
- `test_hdr_selection.py`: compares the Java HDR launch decision with the actual
  codec-selection block from the pinned common-C submodule across 2,025 client
  and host capability combinations. SDP advertises compatible codec profiles;
  capability changes after launch are outside this test's scope.

- `test_audio_track_recovery.py`: full production audio renderer with scripted
  AudioTrack and clock boundaries. Checks partial writes, dead-object recovery,
  recreation cooldown, effect-session balance, failed replacement cleanup and
  interruption of a blocked decoder write before native shutdown joins. Actual
  audio-service recovery and route changes require Android device testing.

- `test_nvhttp_client_policy.py`: compiles the production `NvHTTP.initializeHttpState`
  against a recording OkHttp stub and checks that none of the three clients follows
  redirects (the paired host is addressed directly; a 302 must not steer fetches or
  offer the client certificate elsewhere) while the derived clients keep their timeouts.

- `test_controller_removal_lifecycle.py`: production controller removal/stop
  methods and the actual NvConnection native-send wrapper. Checks normal and
  duplicate removals, late USB/BLE and Android callbacks after stop, and a native
  removal send held across a concurrent stop. Also runs VirtualController
  retransmissions through production OSC state/aggregation and the native wrapper:
  late callbacks cannot mutate retired state, and stop drains an admitted OSC send.
  It verifies session isolation at
  fake native/device boundaries; reconnect behavior still needs hardware checks.

- `test_controller_default_context_stop.py`: text gate that `ControllerHandler.stop()`
  retires the default input context (device id 0 / Android 11 virtual device) so a
  mouse-emulation runnable cannot outlive the stream.
- `test_decoder_csd_bounds.py`: text gate that host-supplied codec configuration fails
  through the decoder's diagnosed `RendererException` path (capacity-checked CSD appends,
  SPS parsed over the unit length, guarded SPS parser/writer).

- `test_game_pip_after_destroy.py`: text gate that `Game.updatePipAutoEnter()` returns on a
  finishing/destroyed activity and that `onDestroy` detaches both USB service listeners
  before unbinding (the service's stop() still reports pending permission prompts).

`test_controller_removal_lifecycle.py` also pauses the production USB/BLE state,
motion, touch and battery forwarding at the native-send boundary. Both `stop()`
and controller removal must wait for an admitted callback, and callbacks that
arrive after retirement must send nothing. The harness exercises 55 checks,
including on-screen retransmission and removal coverage; no hardware is used.

- `test_game_pending_start_stop.py`: text gate that Game marks itself connecting before
  `conn.start()` (so leaving during the spinner stops the pending start), that
  `NvConnection.stop()` releases the process-wide permit only when its start acquired it,
  that the start thread honours a stop requested during the HTTP launch phase, and that
  `connectionStarted()` ignores a destroyed activity.

- `test_decoder_input_wait.py`: text gate that one decoder input-buffer wait is bounded by
  `INPUT_BUFFER_WAIT_MS` (with direct submit it runs on common-c's receive thread), that the
  hung exception is measured across calls, and that direct submit is advertised only for the
  plain 2D render path.
- `test_settings_and_toast.py`: text gate that the custom refresh rate is formatted with
  `Locale.ROOT`, the custom bitrate parse is guarded, and `displayMessage()` never hands a
  null text to Toast.

- `test_custom_stream_rates.py`: executes the production bitrate and refresh-rate
  preference listeners on the JVM with storage, toast and reload boundary fakes.
  Rejects non-finite/out-of-range bitrate values and non-positive rounded refresh
  rates before persistence. Models preference callback persistence to ensure the
  normalized refresh rate survives, including on comma-decimal locales.
  `--baseline` reads HEAD; Android device UI behavior is outside this fixture.

- `test_decoder_input_wait_jvm.py`: executes the production `fetchNextInputBuffer()` on the
  JVM with scripted MediaCodec/clock fakes: one call waits at most `INPUT_BUFFER_WAIT_MS`,
  the hung exception fires once the stall measured from the first failed wait reaches
  `DECODER_HUNG_MS` after exactly one `notifyCrash`, a success restarts the window, and a
  stopping decoder never throws.

- `test_profile_import_io.py`: executes the production URI reader and settings
  activity-result handler against scripted document providers. Read/close errors
  and null streams preserve the existing profile and report an import error;
  missing results are ignored. Covers successful UTF-8 input and the 4 MiB size
  boundary. Android framework, preferences and JSON interpretation are boundary
  fakes; `--baseline` reads HEAD.

- `test_profile_persistence.py`: executes the complete ProfilesManager with real
  Gson and filesystem I/O. Serialization failures, a child-process file-size
  limit, and rename failures must preserve the prior save and clean temporary
  files. Invalid profile collections must not replace the live map or active
  selection; valid UTF-8 saves, empty collections and recovery remain supported.
  Requires Linux and JDK 17+. Uses the app's pinned Gson artifact with SHA-256
  verification; `AUDIT_GSON_JAR` supplies an offline copy. `--baseline` reads HEAD.
  Android context/preferences are fakes; native device filesystem validation is
  separate. The existing workflow glob includes this regression automatically.

- `test_asset_cache_limits.py`: complete production asset loader and cache helper;
  real PNG metadata, bounded decode requests on both Android decoder paths,
  read-error descriptor cleanup and a real buffered close/flush failure using
  Linux `/dev/full`. Android bitmap allocation is recorded by boundary stubs;
  device bitmap decoding/rendering still requires Android integration testing.

- `test_asset_loader_lifecycle.py`: complete production `CachedAppAssetLoader`
  with controlled Android animation/task dispatch. Late fade callbacks cannot
  overwrite a rebound tile; normal disk/placeholder transitions still work.
  Real JVM executors verify idle core-worker retirement, draining active/queued
  work and reuse after retirement. Only the idle timeout is shortened in the
  test; device rendering and Android task scheduling remain integration checks.
