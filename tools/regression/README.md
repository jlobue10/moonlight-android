# Host regression checks

Run each `test_*.py` with Python 3, JDK 17+ (`java` with the `jdk.compiler`
module), and g++ for the Vulkan test. The tests never connect to a controller,
start a stream, or load Android native libraries. They compile production
sources or extract production methods verbatim, with deterministic platform
boundaries. See each script for the exact boundary being replaced.

- `test_pyrowave_recovery.py`: renderer rebuilds, cleanup/replacement during recovery,
  HDR restoration and the terminal error budget, with JNI calls recorded.
- `test_depth_failure_cleanup.py`: failed-model cleanup blocked across stop; the GL
  waiter exits while native cleanup remains on its owning executor.
- `test_steam_ble.py`: actual BLE driver; queue limits, stale callbacks,
  disconnected rumble, subscription failures and write timeout recovery.
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
